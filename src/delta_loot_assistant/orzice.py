from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from .icon_cache import IconRecord
from .models import ItemDefinition, PriceEntry, PricePack
from .pricing import LocalPricePackProvider

ORZICE_SOURCE = "三角洲数据帝开放平台（用户授权的手动日更快照）"
MINIMUM_SYNC_INTERVAL = timedelta(hours=24)


class OrziceApiError(RuntimeError):
    pass


class SyncDeferredError(RuntimeError):
    def __init__(self, next_allowed_at: datetime):
        self.next_allowed_at = next_allowed_at
        super().__init__(
            f"为节省付费资源，24小时内不会重复同步；下次可同步时间："
            f"{next_allowed_at.astimezone().isoformat(timespec='minutes')}"
        )


class OrziceClient:
    BASE_URL = "https://orzice.com/workApi/v1/sjz_api"

    def __init__(self, token: str, timeout_seconds: float = 30):
        normalized = token.strip()
        if not normalized:
            raise ValueError("尚未配置三角洲数据帝 Token")
        self._token = normalized
        self.timeout_seconds = timeout_seconds

    def item_info_all(self) -> list[dict[str, Any]]:
        return self._get("item_info_all")

    def item_price_all(self) -> list[dict[str, Any]]:
        return self._get("item_price_all")

    def _get(self, endpoint: str) -> list[dict[str, Any]]:
        try:
            response = requests.get(
                f"{self.BASE_URL}/{endpoint}",
                params={"token": self._token},
                headers={
                    "User-Agent": "DeltaLootAssistant/0.1",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
                timeout=(10, self.timeout_seconds),
            )
            status_code = response.status_code
            response.raise_for_status()
            payload = response.json()
        except requests.HTTPError as exc:
            raise OrziceApiError(f"数据接口返回 HTTP {status_code}") from exc
        except (requests.RequestException, json.JSONDecodeError) as exc:
            raise OrziceApiError(f"数据接口请求失败（{type(exc).__name__}），未自动重试") from None
        if not isinstance(payload, dict) or int(payload.get("code", -1)) != 0:
            message = payload.get("msg", "未知错误") if isinstance(payload, dict) else "响应无效"
            safe_message = str(message).replace(self._token, "[已隐藏]")
            raise OrziceApiError(f"数据接口拒绝请求：{safe_message}")
        data = payload.get("data")
        if not isinstance(data, list):
            raise OrziceApiError("数据接口没有返回物品列表")
        return [row for row in data if isinstance(row, dict)]


@dataclass(slots=True)
class OrziceSyncResult:
    item_count: int
    priced_item_count: int
    generated_at: datetime
    pack: PricePack


class OrziceCatalogRepository:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    synced_at TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    price_count INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_catalog_snapshots_active
                    ON catalog_snapshots(active, id DESC);
                CREATE TABLE IF NOT EXISTS catalog_items (
                    snapshot_id INTEGER NOT NULL,
                    definition_id TEXT NOT NULL,
                    official_id TEXT NOT NULL,
                    provider_id INTEGER NOT NULL,
                    legacy_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    grade INTEGER NOT NULL,
                    sellable INTEGER NOT NULL,
                    image_url TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (snapshot_id, definition_id),
                    FOREIGN KEY (snapshot_id) REFERENCES catalog_snapshots(id)
                );
                CREATE INDEX IF NOT EXISTS idx_catalog_items_name
                    ON catalog_items(name);
                """
            )

    def last_synced_at(self) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT synced_at FROM catalog_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return datetime.fromisoformat(row["synced_at"]) if row else None

    def save_snapshot(
        self,
        rows: list[dict[str, Any]],
        *,
        generated_at: datetime,
        price_count: int,
    ) -> int:
        synced_at = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("UPDATE catalog_snapshots SET active = 0 WHERE active = 1")
            cursor = connection.execute(
                """
                INSERT INTO catalog_snapshots (
                    synced_at, generated_at, source, item_count, price_count, active
                ) VALUES (?, ?, ?, ?, ?, 1)
                """,
                (
                    synced_at.isoformat(),
                    generated_at.isoformat(),
                    ORZICE_SOURCE,
                    len(rows),
                    price_count,
                ),
            )
            snapshot_id = int(cursor.lastrowid)
            for row in rows:
                definition = item_definition_from_row(row)
                connection.execute(
                    """
                    INSERT INTO catalog_items (
                        snapshot_id, definition_id, official_id, provider_id,
                        legacy_id, name, category, width, height, grade,
                        sellable, image_url, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        definition.id,
                        str(row.get("objectID", "")),
                        _as_int(row.get("oid")),
                        _as_int(row.get("id")),
                        definition.name,
                        definition.category,
                        definition.width,
                        definition.height,
                        definition.grade,
                        int(bool(_as_int(row.get("is_get")))),
                        str(row.get("pic", "")),
                        json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
        return snapshot_id

    def active_definitions(self) -> dict[str, ItemDefinition]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload
                FROM catalog_items
                WHERE snapshot_id = (
                    SELECT id FROM catalog_snapshots
                    WHERE active = 1 ORDER BY id DESC LIMIT 1
                )
                """
            ).fetchall()
        definitions = [
            item_definition_from_row(json.loads(row["payload"])) for row in rows
        ]
        return {definition.id: definition for definition in definitions}

    def active_counts(self) -> tuple[int, int] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT item_count, price_count
                FROM catalog_snapshots
                WHERE active = 1
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        return (int(row["item_count"]), int(row["price_count"])) if row else None

    def active_icon_records(self) -> list[IconRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT definition_id, image_url
                FROM catalog_items
                WHERE snapshot_id = (
                    SELECT id FROM catalog_snapshots
                    WHERE active = 1 ORDER BY id DESC LIMIT 1
                )
                  AND image_url <> ''
                """
            ).fetchall()
        return [
            IconRecord(
                definition_id=str(row["definition_id"]),
                image_url=str(row["image_url"]),
            )
            for row in rows
        ]


class OrziceDailySyncService:
    def __init__(
        self,
        client: OrziceClient,
        catalog_repository: OrziceCatalogRepository,
        price_provider: LocalPricePackProvider,
        minimum_interval: timedelta = MINIMUM_SYNC_INTERVAL,
        minimum_expected_items: int = 500,
        minimum_expected_prices: int = 500,
    ):
        self.client = client
        self.catalog_repository = catalog_repository
        self.price_provider = price_provider
        self.minimum_interval = minimum_interval
        self.minimum_expected_items = minimum_expected_items
        self.minimum_expected_prices = minimum_expected_prices

    def sync(self, *, now: datetime | None = None) -> OrziceSyncResult:
        current_time = now or datetime.now(UTC)
        last_sync = self.catalog_repository.last_synced_at()
        if last_sync is not None:
            next_allowed = last_sync + self.minimum_interval
            if current_time < next_allowed:
                raise SyncDeferredError(next_allowed)

        item_rows = self.client.item_info_all()
        price_rows = self.client.item_price_all()
        if len(item_rows) < self.minimum_expected_items:
            raise OrziceApiError(
                f"基础物品响应不完整：仅 {len(item_rows)} 条，未写入数据库"
            )
        if len(price_rows) < self.minimum_expected_prices:
            raise OrziceApiError(
                f"价格响应不完整：仅 {len(price_rows)} 条，未写入数据库"
            )
        item_by_provider_id = {
            _as_int(row.get("oid")): row
            for row in item_rows
            if _as_int(row.get("oid")) > 0
        }
        prices: dict[str, PriceEntry] = {}
        timestamps: list[int] = []
        for row in price_rows:
            provider_id = _as_int(row.get("id"))
            item_row = item_by_provider_id.get(provider_id)
            if item_row is None:
                continue
            definition_id = definition_id_from_row(item_row)
            prices[definition_id] = PriceEntry(unit_price=max(0, _as_int(row.get("price"))))
            timestamp = _as_int(row.get("is_get_time"))
            if timestamp > 0:
                timestamps.append(timestamp)

        if len(prices) < self.minimum_expected_prices:
            raise OrziceApiError(
                f"只有 {len(prices)} 条价格成功关联物品，未写入数据库"
            )

        generated_at = (
            datetime.fromtimestamp(max(timestamps), tz=UTC)
            if timestamps
            else current_time
        )
        pack = PricePack(
            schema_version=1,
            region="cn",
            season="live-cn",
            generated_at=generated_at,
            source=ORZICE_SOURCE,
            prices=prices,
        )
        self.catalog_repository.save_snapshot(
            item_rows,
            generated_at=generated_at,
            price_count=len(prices),
        )
        self.price_provider.save_pack(pack)
        return OrziceSyncResult(
            item_count=len(item_rows),
            priced_item_count=len(prices),
            generated_at=generated_at,
            pack=pack,
        )


def definition_id_from_row(row: dict[str, Any]) -> str:
    official_id = str(row.get("objectID", "")).strip()
    provider_id = _as_int(row.get("oid"))
    legacy_id = _as_int(row.get("id"))
    if official_id and official_id != "0":
        source_id = provider_id if provider_id > 0 else legacy_id
        return f"df:{official_id}:source:{source_id}"
    source_id = provider_id if provider_id > 0 else legacy_id
    return f"orzice:{source_id}"


def item_definition_from_row(row: dict[str, Any]) -> ItemDefinition:
    primary = str(row.get("primaryClass", "")).strip()
    secondary = str(row.get("secondClass", "")).strip()
    category = _category(primary, secondary)
    common_containers = {"pocket", "chest_rig", "backpack", "safe_box"}
    allowed_containers = set(common_containers)
    safe_box_allowed = primary not in {"gun", "protect"}
    equipment_slots: set[str] = set()
    weapon_slots: set[str] = set()
    if category == "weapon":
        allowed_containers = {"backpack", "weapon_slot"}
        weapon_slots = {"primary", "secondary"}
    elif category == "backpack":
        allowed_containers.add("equipment_slot")
        equipment_slots = {"backpack"}
    elif category == "chest_rig":
        allowed_containers.add("equipment_slot")
        equipment_slots = {"chest_rig"}
    elif category == "magazine":
        allowed_containers.add("weapon_attachment")
    elif category == "ammo":
        allowed_containers.add("magazine")

    aliases = [
        value
        for value in {
            secondary,
            str(row.get("secondClassCN", "")).strip(),
        }
        if value and value != str(row.get("objectName", "")).strip()
    ]
    return ItemDefinition(
        id=definition_id_from_row(row),
        name=str(row.get("objectName", "")).strip() or definition_id_from_row(row),
        aliases=sorted(aliases),
        category=category,
        # The provider calls the vertical span ``width`` and the horizontal
        # span ``length``.  Our grid model follows screen coordinates instead.
        width=max(1, _as_int(row.get("length"), 1)),
        height=max(1, _as_int(row.get("width"), 1)),
        grade=max(1, _as_int(row.get("grade"), 1)),
        stack_limit=60 if category == "ammo" else 1,
        allowed_containers=allowed_containers,
        safe_box_allowed=safe_box_allowed,
        # The API snapshot has no per-item rotation restriction. Inventory
        # screenshots show long guns in both horizontal and vertical layouts,
        # so both orientations stay legal until a verified rule says otherwise.
        fixed_orientation=False,
        weapon_slots=weapon_slots,
        equipment_slots=equipment_slots,
        caliber=secondary if category == "ammo" else None,
    )


def _category(primary: str, secondary: str) -> str:
    if primary == "gun":
        return "weapon"
    if primary == "ammo":
        return "ammo"
    if primary == "acc":
        return "magazine" if secondary == "弹匣" else "attachment"
    if primary == "protect":
        return {
            "背包": "backpack",
            "胸挂": "chest_rig",
            "护甲": "armor",
            "头盔": "helmet",
        }.get(secondary, "equipment")
    if primary == "props":
        return {
            "消耗品": "consumable",
            "钥匙": "key",
        }.get(secondary, "collectible")
    if primary == "exchange" and secondary == "货币":
        return "currency"
    return "collectible"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
