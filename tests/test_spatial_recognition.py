import io
import json

import cv2
import numpy as np
import pytest

from delta_loot_assistant.agent_workflow import local_llm
from delta_loot_assistant.models import ItemDefinition
from delta_loot_assistant.recognition import OcrLine
from delta_loot_assistant.spatial_recognition import (
    collect_lines,
    same_label,
    visible_panels,
    weapon_rect,
)


def test_vertical_weapon_uses_footer_to_avoid_neighboring_items():
    gun = ItemDefinition("m7", "M7", [], "weapon", 5, 2)
    lines = [OcrLine("0/45", .99, (195, 508, 30, 12))]
    rect = weapon_rect(100, 200, gun, 64, lines, 1920, 1080)
    assert rect[2] < 140 and rect[3] > 300
    moved = weapon_rect(300, 100, gun, 64,
                        [OcrLine("0/45", .99, (395, 408, 30, 12))], 1920, 1080)
    assert moved[2:] == rect[2:]


def test_horizontal_weapon_keeps_horizontal_orientation():
    gun = ItemDefinition("m7", "M7", [], "weapon", 5, 2)
    rect = weapon_rect(100, 200, gun, 64,
                       [OcrLine("0/45", .99, (386, 314, 30, 12))], 1920, 1080)
    assert rect[2] > 300 and rect[3] < 140


def test_same_text_in_adjacent_cells_is_not_a_duplicate():
    a = OcrLine("AWM", .9, (100, 100, 30, 15))
    assert same_label(a, OcrLine("AWM", .9, (102, 101, 30, 15)))
    assert not same_label(a, OcrLine("AWM", .9, (164, 100, 30, 15)))


def test_tile_coordinates_are_mapped_back_and_ocr_is_bounded():
    class Ocr:
        calls = 0

        def recognize_lines(self, _image):
            self.calls += 1
            return [] if self.calls != 2 else [OcrLine("AWM", .9, (100, 100, 40, 20))]

    ocr = Ocr()
    lines = collect_lines(np.zeros((1080, 1920, 3), np.uint8), ocr, True)
    assert len(lines) == 1 and lines[0].rect == (50, 50, 20, 10)
    assert ocr.calls == 7


def test_visible_slot_rectangles_follow_pixels_not_fixed_positions():
    image = np.zeros((500, 700, 3), np.uint8)
    cv2.rectangle(image, (310, 130), (438, 258), (180, 180, 180), 1)
    panels = visible_panels(image, 64)
    assert any(abs(x-310) < 4 and abs(y-130) < 4 for x, y, _w, _h in panels)


def test_single_cell_item_cannot_expand_to_entire_container(catalog, monkeypatch):
    from delta_loot_assistant import adaptive_recognition

    monkeypatch.setattr(adaptive_recognition, "collect_lines", lambda *_args: [
        OcrLine("高价值小物品", .99, (101, 201, 50, 12))])
    monkeypatch.setattr(adaptive_recognition, "visible_panels", lambda *_args: [
        (100, 200, 192, 192)])
    result = adaptive_recognition.propose_items(
        np.zeros((1080, 1920, 3), np.uint8), catalog, None, enhanced=True)
    assert result[0]["rect"][2:] == [64, 64]


def test_successful_structured_model_output_uses_fixed_local_endpoint(monkeypatch):
    from delta_loot_assistant import agent_workflow

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "http://127.0.0.1:11434/api/chat"
            payload = json.loads(request.data)
            assert payload["stream"] is False and payload["format"]["type"] == "object"
            assert timeout == 40
            output = {"summary": "整枪需要核对配件", "evidence_ids": ["rule:weapon"]}
            return io.BytesIO(json.dumps({"message": {"content": json.dumps(output)}}).encode())

    monkeypatch.setattr(agent_workflow, "build_opener", lambda *_args: Opener())
    result = local_llm("整枪", [{"id": "rule:weapon", "text": "test"}], "test-model")
    assert result["evidence_ids"] == ["rule:weapon"]


@pytest.mark.parametrize("refs", [["invented"], [], "rule:weapon", [123]])
def test_model_output_rejects_unknown_or_malformed_citations(monkeypatch, refs):
    from delta_loot_assistant import agent_workflow

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "http://127.0.0.1:11434/api/chat"
            output = {"summary": "解释", "evidence_ids": refs}
            return io.BytesIO(json.dumps({"message": {"content": json.dumps(output)}}).encode())

    monkeypatch.setattr(agent_workflow, "build_opener", lambda *_args: Opener())
    with pytest.raises(ValueError):
        local_llm("解释", [{"id": "rule:weapon", "text": "test"}], "test-model")


def test_model_name_validation_happens_before_network():
    with pytest.raises(ValueError):
        local_llm("test", [], "bad model\nsecret")
