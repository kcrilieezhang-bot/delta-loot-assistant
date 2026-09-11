from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from .models import InventoryState, ItemInstance, Origin
from .recognition import RecognitionResult, Recognizer


@dataclass(slots=True)
class ScanSession:
    recognizer: Recognizer
    state: InventoryState | None = None
    frame_hashes: set[str] = field(default_factory=set)
    capture_count: int = 0
    needs_more_captures: bool = False

    def add_capture(self, image: np.ndarray) -> RecognitionResult:
        result = self.recognizer.recognize(image)
        if result.frame_hash in self.frame_hashes:
            result.state.warnings.append("检测到重复截图，本次未重复添加物品")
            return result
        self.frame_hashes.add(result.frame_hash)
        self.capture_count += 1
        self.needs_more_captures = result.needs_more_captures
        self.state = self._merge(self.state, result.state)
        return result

    def resolve_recognition(
        self,
        recognition_id: str,
        definition_id: str,
        *,
        quantity: int = 1,
        manual_unit_price: int | None = None,
    ) -> ItemInstance:
        if self.state is None:
            raise RuntimeError("扫描会话尚未开始")
        unresolved = next(
            (
                entry
                for entry in self.state.unresolved
                if entry.recognition_id == recognition_id
            ),
            None,
        )
        if unresolved is None:
            raise KeyError(recognition_id)
        provisional_id = unresolved.metadata.get("provisional_instance_id")
        item = (
            self.state.item_by_id(str(provisional_id))
            if provisional_id is not None
            else None
        )
        if item is None:
            item = ItemInstance(
                definition_id=definition_id,
                quantity=max(1, int(unresolved.metadata.get("quantity", quantity))),
                origin=Origin(unresolved.metadata.get("origin", Origin.LOOT.value)),
                source_container_id=unresolved.metadata.get("source_container_id"),
                confidence=1.0,
                source_capture_id=unresolved.source_capture_id,
                manual_unit_price=manual_unit_price,
                metadata={
                    **unresolved.metadata,
                    "resolved_from": recognition_id,
                },
            )
            self.state.items.append(item)
        else:
            item.definition_id = definition_id
            item.confidence = 1.0
            item.metadata["resolved_from"] = recognition_id
            if manual_unit_price is not None:
                item.manual_unit_price = manual_unit_price
        item.metadata["review_required"] = False
        self.state.unresolved = [
            entry
            for entry in self.state.unresolved
            if entry.recognition_id != recognition_id
        ]
        return item

    def complete(self, force: bool = False) -> InventoryState:
        if self.state is None:
            raise RuntimeError("请先按F8扫描理包界面")
        if self.state.unresolved and not force:
            raise RuntimeError(f"仍有 {len(self.state.unresolved)} 个物品需要确认")
        self.state.complete_scan = True
        self.needs_more_captures = False
        return self.state

    def reset(self) -> None:
        self.state = None
        self.frame_hashes.clear()
        self.capture_count = 0
        self.needs_more_captures = False

    @staticmethod
    def _merge(
        current: InventoryState | None, incoming: InventoryState
    ) -> InventoryState:
        if current is None:
            return incoming
        existing_keys = {
            ScanSession._dedupe_key(item)
            for item in current.items
        }
        offsets = ScanSession._estimate_scroll_offsets(current, incoming)
        for item in incoming.items:
            key = ScanSession._dedupe_key(item)
            if key not in existing_keys and not ScanSession._matches_scrolled_item(
                item,
                current.items,
                offsets,
            ):
                current.items.append(item)
                existing_keys.add(key)
        unresolved_keys = {
            (
                entry.reason,
                tuple(entry.candidate_definition_ids),
                entry.source_capture_id,
            )
            for entry in current.unresolved
        }
        for entry in incoming.unresolved:
            key = (
                entry.reason,
                tuple(entry.candidate_definition_ids),
                entry.source_capture_id,
            )
            if key not in unresolved_keys:
                current.unresolved.append(entry)
                unresolved_keys.add(key)
        current.capture_ids.extend(
            capture_id
            for capture_id in incoming.capture_ids
            if capture_id not in current.capture_ids
        )
        current.warnings.extend(
            warning for warning in incoming.warnings if warning not in current.warnings
        )
        current.containers.update(incoming.containers)
        current.complete_scan = False
        return current

    @staticmethod
    def _dedupe_key(item: ItemInstance) -> tuple[object, ...]:
        return (
            item.definition_id,
            item.origin.value,
            item.source_container_id,
            item.quantity,
            item.metadata.get("visual_hash"),
            tuple(item.metadata.get("rect", ())),
        )

    @staticmethod
    def _estimate_scroll_offsets(
        current: InventoryState,
        incoming: InventoryState,
    ) -> dict[str, tuple[int, int]]:
        if not current.capture_ids:
            return {}
        last_capture_id = current.capture_ids[-1]
        previous = [
            item
            for item in current.items
            if item.source_capture_id == last_capture_id
        ]
        previous_by_fingerprint: dict[
            tuple[object, ...], list[ItemInstance]
        ] = defaultdict(list)
        incoming_by_fingerprint: dict[
            tuple[object, ...], list[ItemInstance]
        ] = defaultdict(list)
        for item in previous:
            previous_by_fingerprint[ScanSession._visual_fingerprint(item)].append(
                item
            )
        for item in incoming.items:
            incoming_by_fingerprint[ScanSession._visual_fingerprint(item)].append(
                item
            )

        votes: dict[str, Counter[tuple[int, int]]] = defaultdict(Counter)
        for fingerprint, previous_items in previous_by_fingerprint.items():
            incoming_items = incoming_by_fingerprint.get(fingerprint, [])
            if len(previous_items) != 1 or len(incoming_items) != 1:
                continue
            previous_rect = ScanSession._rect(previous_items[0])
            incoming_rect = ScanSession._rect(incoming_items[0])
            if previous_rect is None or incoming_rect is None:
                continue
            container_id = str(fingerprint[2])
            dx = round((incoming_rect[0] - previous_rect[0]) / 4) * 4
            dy = round((incoming_rect[1] - previous_rect[1]) / 4) * 4
            votes[container_id][(dx, dy)] += 1

        offsets: dict[str, tuple[int, int]] = {}
        for container_id, counter in votes.items():
            offset, count = counter.most_common(1)[0]
            if count >= 2:
                offsets[container_id] = offset
        return offsets

    @staticmethod
    def _matches_scrolled_item(
        incoming: ItemInstance,
        existing: list[ItemInstance],
        offsets: dict[str, tuple[int, int]],
    ) -> bool:
        container_id = incoming.source_container_id or ""
        offset = offsets.get(container_id)
        incoming_rect = ScanSession._rect(incoming)
        if offset is None or incoming_rect is None:
            return False
        fingerprint = ScanSession._visual_fingerprint(incoming)
        adjusted_x = incoming_rect[0] - offset[0]
        adjusted_y = incoming_rect[1] - offset[1]
        for item in existing:
            if ScanSession._visual_fingerprint(item) != fingerprint:
                continue
            rect = ScanSession._rect(item)
            if rect is None:
                continue
            if abs(rect[0] - adjusted_x) <= 6 and abs(rect[1] - adjusted_y) <= 6:
                return True
        return False

    @staticmethod
    def _visual_fingerprint(item: ItemInstance) -> tuple[object, ...]:
        return (
            item.definition_id,
            item.origin.value,
            item.source_container_id,
            item.quantity,
            item.metadata.get("visual_hash"),
        )

    @staticmethod
    def _rect(item: ItemInstance) -> tuple[int, int, int, int] | None:
        rect = item.metadata.get("rect")
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            return None
        return tuple(int(value) for value in rect)
