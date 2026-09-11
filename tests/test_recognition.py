from __future__ import annotations

import numpy as np
import pytest

from delta_loot_assistant.models import Origin
from delta_loot_assistant.recognition import (
    HybridRecognizer,
    LayoutProfile,
    OcrEngine,
    OcrLine,
    RegionProfile,
    load_layout_profile,
    save_layout_profile,
)
from delta_loot_assistant.scan_session import ScanSession


def test_recognizer_rejects_unsupported_resolution(catalog, tmp_path) -> None:
    recognizer = HybridRecognizer(catalog, tmp_path)

    with pytest.raises(ValueError, match="1920"):
        recognizer.recognize(np.zeros((720, 1280, 3), dtype=np.uint8))


def test_layout_profile_round_trip(tmp_path) -> None:
    profile = LayoutProfile.delta_force_1080p()
    profile.regions[0].rect = (1, 2, 300, 400)
    path = tmp_path / "layout.json"

    save_layout_profile(path, profile)
    restored = load_layout_profile(path)

    assert restored.resolution == (1920, 1080)
    assert restored.regions[0].rect == (1, 2, 300, 400)
    assert restored.regions[0].cell_size == 65
    assert restored.regions[0].columns == 5


def test_grid_ocr_preserves_quantity_and_manual_review(catalog, tmp_path) -> None:
    class FixtureOcr(OcrEngine):
        def recognize(self, crop):
            return ""

        def recognize_lines(self, crop):
            return [
                OcrLine("弹药", 0.99, (4, 3, 30, 15)),
                OcrLine("36", 0.99, (43, 49, 19, 15)),
            ]

    layout = LayoutProfile((1920, 1080), [
        RegionProfile("backpack", "背包", "backpack", (100, 100, 165, 165),
                      Origin.CARRIED, cell_size=65, columns=1, rows=1),
    ])
    recognizer = HybridRecognizer(catalog, tmp_path, layout=layout, ocr=FixtureOcr())
    session = ScanSession(recognizer)
    result = session.add_capture(np.zeros((1080, 1920, 3), dtype=np.uint8))

    assert len(result.state.items) == 1
    item = result.state.items[0]
    assert item.quantity == 36
    assert item.origin == Origin.CARRIED
    assert item.metadata["rect"] == (100, 100, 65, 65)
    assert result.state.unresolved
    with pytest.raises(RuntimeError, match="需要确认"):
        session.complete()
    resolved = session.resolve_recognition(result.state.unresolved[0].recognition_id, "ammo")
    assert resolved is item
    assert len(session.state.items) == 1
    assert resolved.quantity == 36
