from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QObject, Signal

from .capture import ScreenCaptureService
from .catalog import Catalog
from .icon_cache import IconTemplateCache
from .models import InventoryState, ItemInstance, Origin, PlanResult
from .optimizer import CpSatInventoryOptimizer
from .orzice import OrziceCatalogRepository, OrziceClient, OrziceDailySyncService
from .paths import (
    app_config_path,
    contribution_root,
    default_database_path,
    layout_profile_path,
    orzice_token_path,
    sample_catalog_path,
    sample_price_pack_path,
    template_root,
)
from .pricing import LocalPricePackProvider, ValuationService
from .recognition import (
    HybridRecognizer,
    RapidOcrEngine,
    RecognitionResult,
    load_layout_profile,
    save_layout_profile,
)
from .scan_session import ScanSession
from .secrets import DpapiTokenStore
from .value_ranking import InventoryValueRanker, RankedItemValue


class AssistantController(QObject):
    state_changed = Signal(object)
    catalog_changed = Signal()
    plan_ready = Signal(object)
    capture_ready = Signal(object)
    status_message = Signal(str)
    error_message = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.config = json.loads(app_config_path().read_text(encoding="utf-8"))
        self.catalog = Catalog.load(sample_catalog_path())
        database_path = default_database_path()
        self.price_provider = LocalPricePackProvider(database_path)
        self.catalog_repository = OrziceCatalogRepository(database_path)
        self.catalog.items.update(self.catalog_repository.active_definitions())
        self.token_store = DpapiTokenStore(orzice_token_path())
        if self.price_provider.current_pack() is None:
            self.price_provider.import_pack(sample_price_pack_path())
        self.valuation = ValuationService(self.price_provider)
        self.value_ranker = InventoryValueRanker(self.catalog, self.valuation)
        recognition_config = self.config["recognition"]
        saved_layout = layout_profile_path()
        layout = load_layout_profile(saved_layout) if saved_layout.exists() else None
        ocr = RapidOcrEngine()
        self.recognizer = HybridRecognizer(
            self.catalog,
            template_root(),
            layout=layout,
            ocr=ocr,
            high_confidence=float(recognition_config["high_confidence"]),
            medium_confidence=float(recognition_config["medium_confidence"]),
        )
        self.scan_session = ScanSession(self.recognizer)
        solver_config = self.config["solver"]
        self.optimizer = CpSatInventoryOptimizer(
            self.catalog,
            self.valuation,
            max_time_seconds=float(solver_config["max_time_seconds"]),
            worker_count=int(solver_config["worker_count"]),
        )
        self.capture_service = ScreenCaptureService((1920, 1080))
        self.last_image: np.ndarray | None = None
        self.last_plan: PlanResult | None = None

    def has_orzice_token(self) -> bool:
        return self.token_store.load() is not None

    def save_orzice_token(self, token: str) -> None:
        self.token_store.save(token)
        self.status_message.emit("Token 已使用 Windows DPAPI 加密保存")

    def sync_orzice_snapshot(self) -> None:
        try:
            token = self.token_store.load()
            if token is None:
                raise ValueError("请先保存三角洲数据帝 Token")
            service = OrziceDailySyncService(
                OrziceClient(token),
                self.catalog_repository,
                self.price_provider,
            )
            result = service.sync()
            self.catalog.items.update(self.catalog_repository.active_definitions())
            self.catalog.season = result.pack.season
            icon_message = ""
            try:
                icon_result = IconTemplateCache(template_root()).build(
                    self.catalog_repository.active_icon_records()
                )
                icon_message = f"；本地图标 {icon_result.item_count} 项"
            except Exception:
                icon_message = "；图标缓存未完成，价格快照已保存，无需重复日更"
            self.recognizer.matcher.reload()
            self.catalog_changed.emit()
            self.state_changed.emit(self.state)
            self.status_message.emit(
                f"日更快照完成：{result.item_count} 个物品，"
                f"{result.priced_item_count} 个快照价格{icon_message}"
            )
        except Exception as exc:
            self.error_message.emit(f"日更快照失败：{exc}")

    def carried_value_ranking(self) -> list[RankedItemValue]:
        if self.state is None:
            return []
        return self.value_ranker.rank_carried_items(self.state)

    def save_calibration(self) -> None:
        save_layout_profile(layout_profile_path(), self.recognizer.layout)

    @property
    def state(self) -> InventoryState | None:
        return self.scan_session.state

    def capture(self) -> None:
        try:
            image = self.capture_service.capture_active_monitor()
            result = self.scan_session.add_capture(image)
            self.last_image = image
            self.capture_ready.emit(image)
            self.state_changed.emit(self.scan_session.state)
            self._announce_capture(result)
        except Exception as exc:
            self.error_message.emit(str(exc))

    def add_image(self, image: np.ndarray) -> None:
        try:
            result = self.scan_session.add_capture(image)
            self.last_image = image
            self.capture_ready.emit(image)
            self.state_changed.emit(self.scan_session.state)
            self._announce_capture(result)
        except Exception as exc:
            self.error_message.emit(str(exc))

    def solve(self) -> None:
        try:
            state = self.scan_session.complete()
            if any(item.source_capture_id for item in state.items):
                lowest = self.value_ranker.lowest_replaceable(state)
                self.state_changed.emit(state)
                self.status_message.emit(
                    f"估价确认完成：单格价值最低为 {lowest.name}"
                    if lowest else "估价仍有缺口，请补齐缺价、整枪配件或锁定暂不替换的物品"
                )
                return
            self.last_plan = self.optimizer.solve(state)
            self.plan_ready.emit(self.last_plan)
            self.status_message.emit(self.last_plan.headline)
        except Exception as exc:
            self.error_message.emit(str(exc))

    def solve_force_demo(self) -> None:
        if self.state is None:
            self.load_demo_state()
        if self.state:
            self.state.unresolved.clear()
            self.state.complete_scan = True
            self.last_plan = self.optimizer.solve(self.state)
            self.plan_ready.emit(self.last_plan)
            self.status_message.emit(self.last_plan.headline)

    def import_price_pack(self, path: str | Path) -> None:
        try:
            pack = self.price_provider.import_pack(path)
            self.status_message.emit(
                f"已导入价格包：{pack.season} / {pack.generated_at.isoformat()}"
            )
            self.state_changed.emit(self.state)
        except Exception as exc:
            self.error_message.emit(f"价格包导入失败：{exc}")

    def rollback_price_pack(self) -> None:
        pack = self.price_provider.rollback()
        if pack:
            self.status_message.emit(f"已回滚到价格包：{pack.season}")
        else:
            self.error_message.emit("没有可回滚的上一版价格包")

    def update_item(
        self,
        instance_id: str,
        *,
        definition_id: str | None = None,
        quantity: int | None = None,
        durability: float | None = None,
        manual_unit_price: int | None = None,
        locked: bool | None = None,
    ) -> None:
        if self.state is None:
            return
        item = self.state.item_by_id(instance_id)
        if item is None:
            return
        if definition_id is not None and definition_id in self.catalog.items:
            item.definition_id = definition_id
            item.confidence = 1.0
        if quantity is not None:
            item.quantity = max(1, quantity)
        if durability is not None:
            item.durability = max(0.0, min(1.0, durability))
        if manual_unit_price is not None:
            item.manual_unit_price = max(0, manual_unit_price)
            item.metadata["weapon_details_pending"] = False
        if locked is not None:
            item.locked = locked
        self.state_changed.emit(self.state)

    def add_manual_item(
        self,
        definition_id: str,
        *,
        origin: Origin = Origin.LOOT,
        quantity: int = 1,
    ) -> ItemInstance:
        if definition_id not in self.catalog.items:
            raise KeyError(definition_id)
        if self.scan_session.state is None:
            self.scan_session.state = InventoryState(
                containers=deepcopy(self.catalog.containers),
                items=[],
            )
        item = ItemInstance(
            definition_id=definition_id,
            origin=origin,
            quantity=max(1, quantity),
            confidence=1.0,
            metadata={"manual": True},
        )
        self.scan_session.state.items.append(item)
        self.state_changed.emit(self.scan_session.state)
        return item

    def remove_item(self, instance_id: str) -> None:
        if self.state is None:
            return
        self.state.items = [
            item for item in self.state.items if item.instance_id != instance_id
        ]
        self.state_changed.emit(self.state)

    def resolve_unknown(
        self, recognition_id: str, definition_id: str
    ) -> ItemInstance:
        item = self.scan_session.resolve_recognition(recognition_id, definition_id)
        self.state_changed.emit(self.state)
        return item

    def set_item_installation(self, instance_id: str, target: str) -> None:
        if self.state is None:
            return
        item = self.state.item_by_id(instance_id)
        if item is None:
            return
        definition = self.catalog.items[item.definition_id]
        if definition.category != "attachment":
            if target.strip():
                raise ValueError("只有枪械配件可以设置装配关系")
            return

        normalized = target.strip()
        if not normalized:
            self._detach_item(item)
            self.state_changed.emit(self.state)
            return

        weapons = [
            candidate
            for candidate in self.state.items
            if self.catalog.items[candidate.definition_id].category == "weapon"
        ]
        matches = [
            weapon
            for weapon in weapons
            if weapon.instance_id == normalized
            or weapon.instance_id.startswith(normalized)
            or self.catalog.items[weapon.definition_id].name == normalized
        ]
        if len(matches) != 1:
            raise ValueError("请输入唯一的枪械名称或实例ID前缀")
        weapon = matches[0]
        if (
            definition.compatible_weapons
            and weapon.definition_id not in definition.compatible_weapons
        ):
            raise ValueError("该配件与目标枪械不兼容")
        slot = definition.attachment_type
        weapon_definition = self.catalog.items[weapon.definition_id]
        if not slot or weapon_definition.attachment_slots.get(slot, 0) <= 0:
            raise ValueError("目标枪械没有对应配件槽位")
        occupied = sum(
            1
            for child_id in weapon.child_instance_ids
            if (
                (child := self.state.item_by_id(child_id)) is not None
                and child.instance_id != item.instance_id
                and self.catalog.items[child.definition_id].attachment_type == slot
            )
        )
        if occupied >= weapon_definition.attachment_slots[slot]:
            raise ValueError("目标枪械的对应槽位已满")
        self._detach_item(item)
        item.installed_on = weapon.instance_id
        item.installed_slot = slot
        if item.instance_id not in weapon.child_instance_ids:
            weapon.child_instance_ids.append(item.instance_id)
        self.state_changed.emit(self.state)

    def _detach_item(self, item: ItemInstance) -> None:
        if self.state is None:
            return
        if item.installed_on:
            old_parent = self.state.item_by_id(item.installed_on)
            if old_parent:
                old_parent.child_instance_ids = [
                    child_id
                    for child_id in old_parent.child_instance_ids
                    if child_id != item.instance_id
                ]
        item.installed_on = None
        item.installed_slot = None

    def save_confirmed_sample(self, instance_id: str) -> Path | None:
        if self.state is None:
            return None
        item = self.state.item_by_id(instance_id)
        if item is None:
            return None
        crop = item.metadata.get("_crop")
        if not isinstance(crop, np.ndarray) or crop.size == 0:
            return None
        item_dir = contribution_root() / item.definition_id
        item_dir.mkdir(parents=True, exist_ok=True)
        image_path = item_dir / f"{item.instance_id}.png"
        if not cv2.imwrite(str(image_path), crop):
            raise RuntimeError("识别样本裁剪保存失败")
        label_path = contribution_root() / "labels.jsonl"
        label = {
            "instance_id": item.instance_id,
            "definition_id": item.definition_id,
            "confirmed_at": datetime.now(UTC).isoformat(),
            "image": str(image_path.relative_to(contribution_root())),
            "source_capture_id": item.source_capture_id,
            "ocr_text": item.metadata.get("ocr_text", ""),
        }
        with label_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(label, ensure_ascii=False) + "\n")
        item.metadata.pop("_crop", None)
        return image_path

    def reset(self) -> None:
        self.scan_session.reset()
        self.last_image = None
        self.last_plan = None
        self.state_changed.emit(None)
        self.status_message.emit("已清空本次扫描")

    def load_demo_state(self) -> None:
        state = InventoryState(
            containers=deepcopy(self.catalog.containers),
            items=[
                ItemInstance(
                    "demo_medkit",
                    origin=Origin.CARRIED,
                    source_container_id="backpack",
                    source_x=0,
                    source_y=0,
                    locked=True,
                ),
                ItemInstance(
                    "demo_drive",
                    origin=Origin.CARRIED,
                    source_container_id="safe_box",
                    source_x=0,
                    source_y=0,
                ),
                ItemInstance("demo_gold_bar", origin=Origin.LOOT),
                ItemInstance("demo_gold_bar", origin=Origin.LOOT),
                ItemInstance("demo_drive", origin=Origin.LOOT),
                ItemInstance("demo_ammo", origin=Origin.LOOT, quantity=60),
                ItemInstance("demo_rifle", origin=Origin.LOOT),
                ItemInstance("demo_optic", origin=Origin.LOOT),
            ],
            capture_ids=["demo"],
            warnings=["当前为演示状态，价格与物品均非真实游戏数据"],
            complete_scan=True,
        )
        self.scan_session.state = state
        self.state_changed.emit(state)
        self.status_message.emit("已载入演示数据，可直接计算")

    def _announce_capture(self, result: RecognitionResult) -> None:
        state = self.scan_session.state
        if result.needs_more_captures:
            self.status_message.emit(
                f"已合并第 {self.scan_session.capture_count} 张截图；"
                f"请补拍滚动区域或确认 {len(state.unresolved) if state else 0} 个未知物品"
            )
        else:
            self.status_message.emit("截图识别完成，可按F9计算")
