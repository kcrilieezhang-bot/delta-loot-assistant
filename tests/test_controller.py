from __future__ import annotations

import json

import numpy as np

from delta_loot_assistant.controller import AssistantController


def test_attachment_editing_and_confirmed_crop_contribution(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    controller = AssistantController()
    controller.load_demo_state()
    weapon = next(
        item for item in controller.state.items if item.definition_id == "demo_rifle"
    )
    optic = next(
        item for item in controller.state.items if item.definition_id == "demo_optic"
    )

    controller.set_item_installation(optic.instance_id, weapon.instance_id[:6])

    assert optic.installed_on == weapon.instance_id
    assert optic.instance_id in weapon.child_instance_ids

    optic.metadata["_crop"] = np.zeros((20, 20, 3), dtype=np.uint8)
    sample_path = controller.save_confirmed_sample(optic.instance_id)

    assert sample_path is not None and sample_path.exists()
    label_path = tmp_path / "DeltaLootAssistant" / "contributed_samples" / "labels.jsonl"
    label = json.loads(label_path.read_text(encoding="utf-8").splitlines()[0])
    assert label["definition_id"] == "demo_optic"
    assert "_crop" not in optic.metadata
