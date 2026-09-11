from __future__ import annotations

from collections import deque

import numpy as np

from delta_loot_assistant.models import (
    ContainerDefinition,
    InventoryState,
    ItemInstance,
    UnresolvedRecognition,
)
from delta_loot_assistant.recognition import RecognitionResult, Recognizer
from delta_loot_assistant.scan_session import ScanSession


class FakeRecognizer(Recognizer):
    def __init__(self, results: list[RecognitionResult]):
        self.results = deque(results)

    def recognize(self, image: np.ndarray) -> RecognitionResult:
        return self.results.popleft()


def result(frame_hash: str, items: list[ItemInstance]) -> RecognitionResult:
    state = InventoryState(
        {"backpack": ContainerDefinition("backpack", "背包", "backpack", 4, 4)},
        items,
        capture_ids=[frame_hash],
    )
    return RecognitionResult(frame_hash, state, True, frame_hash)


def test_duplicate_frame_is_not_merged_twice() -> None:
    item = ItemInstance(
        "x",
        metadata={"visual_hash": "same", "rect": [0, 0, 10, 10]},
    )
    session = ScanSession(FakeRecognizer([result("a", [item]), result("a", [item])]))

    session.add_capture(np.zeros((1, 1), dtype=np.uint8))
    session.add_capture(np.zeros((1, 1), dtype=np.uint8))

    assert session.capture_count == 1
    assert len(session.state.items) == 1


def test_identical_items_at_different_positions_are_preserved() -> None:
    first = ItemInstance(
        "x",
        metadata={"visual_hash": "same", "rect": [0, 0, 10, 10]},
    )
    second = ItemInstance(
        "x",
        metadata={"visual_hash": "same", "rect": [20, 0, 10, 10]},
    )
    session = ScanSession(
        FakeRecognizer([result("a", [first]), result("b", [second])])
    )

    session.add_capture(np.zeros((1, 1), dtype=np.uint8))
    session.add_capture(np.zeros((1, 1), dtype=np.uint8))

    assert len(session.state.items) == 2


def test_scroll_offset_deduplicates_overlap_with_two_anchors() -> None:
    first_page = [
        ItemInstance(
            "a",
            source_capture_id="a",
            source_container_id="backpack",
            metadata={"visual_hash": "anchor-1", "rect": [10, 100, 10, 10]},
        ),
        ItemInstance(
            "b",
            source_capture_id="a",
            source_container_id="backpack",
            metadata={"visual_hash": "anchor-2", "rect": [30, 140, 10, 10]},
        ),
    ]
    second_page = [
        ItemInstance(
            "a",
            source_capture_id="b",
            source_container_id="backpack",
            metadata={"visual_hash": "anchor-1", "rect": [10, 60, 10, 10]},
        ),
        ItemInstance(
            "b",
            source_capture_id="b",
            source_container_id="backpack",
            metadata={"visual_hash": "anchor-2", "rect": [30, 100, 10, 10]},
        ),
        ItemInstance(
            "c",
            source_capture_id="b",
            source_container_id="backpack",
            metadata={"visual_hash": "new", "rect": [50, 160, 10, 10]},
        ),
    ]
    session = ScanSession(
        FakeRecognizer([result("a", first_page), result("b", second_page)])
    )

    session.add_capture(np.zeros((1, 1), dtype=np.uint8))
    session.add_capture(np.zeros((1, 1), dtype=np.uint8))

    assert [item.definition_id for item in session.state.items] == ["a", "b", "c"]


def test_complete_requires_manual_resolution() -> None:
    session = ScanSession(FakeRecognizer([]))
    session.state = InventoryState({}, [], complete_scan=False)
    session.complete()
    assert session.state.complete_scan


def test_resolving_medium_confidence_item_updates_provisional_item() -> None:
    provisional = ItemInstance("old", instance_id="provisional", confidence=0.7)
    initial = result("a", [provisional])
    initial.state.unresolved.append(
        UnresolvedRecognition(
            recognition_id="uncertain",
            confidence=0.7,
            candidate_definition_ids=["old", "new"],
            metadata={"provisional_instance_id": provisional.instance_id},
        )
    )
    session = ScanSession(FakeRecognizer([initial]))
    session.add_capture(np.zeros((1, 1), dtype=np.uint8))

    resolved = session.resolve_recognition("uncertain", "new")

    assert resolved is provisional
    assert resolved.definition_id == "new"
    assert resolved.confidence == 1.0
    assert len(session.state.items) == 1
    assert session.state.unresolved == []
