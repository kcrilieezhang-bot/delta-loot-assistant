from __future__ import annotations

from delta_loot_assistant.action_planner import ActionPlanner
from delta_loot_assistant.models import (
    ActionType,
    ContainerDefinition,
    InventoryState,
    ItemInstance,
    Origin,
    Placement,
)


def test_cycle_without_free_cell_gets_explicit_temporary_step(catalog) -> None:
    left = ContainerDefinition("left", "左容器", "backpack", 1, 1)
    right = ContainerDefinition("right", "右容器", "backpack", 1, 1)
    first = ItemInstance(
        "small_high",
        origin=Origin.CARRIED,
        source_container_id="left",
    )
    second = ItemInstance(
        "small_low",
        origin=Origin.CARRIED,
        source_container_id="right",
    )
    state = InventoryState(
        {"left": left, "right": right},
        [first, second],
        complete_scan=True,
    )
    placements = [
        Placement(first.instance_id, "right", 0, 0, 1, 1),
        Placement(second.instance_id, "left", 0, 0, 1, 1),
    ]

    actions = ActionPlanner(catalog).build(
        state,
        placements,
        {first.instance_id, second.instance_id},
        {first.instance_id: 110, second.instance_id: 10},
    )

    assert actions[0].action_type == ActionType.SWAP
    assert "没有临时空格" in actions[0].instruction
