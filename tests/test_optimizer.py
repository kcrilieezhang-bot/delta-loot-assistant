from __future__ import annotations

import itertools
import random

from delta_loot_assistant.models import (
    ContainerDefinition,
    InventoryState,
    ItemDefinition,
    ItemInstance,
    Origin,
    SolveStatus,
    UnresolvedRecognition,
)
from delta_loot_assistant.optimizer import CpSatInventoryOptimizer


def make_optimizer(catalog, valuation) -> CpSatInventoryOptimizer:
    return CpSatInventoryOptimizer(
        catalog,
        valuation,
        max_time_seconds=2,
        worker_count=1,
    )


def test_two_dimensional_packing_prefers_higher_total(catalog, valuation) -> None:
    items = [ItemInstance("large")] + [
        ItemInstance("small_high") for _ in range(4)
    ]
    state = InventoryState(
        {"backpack": catalog.containers["backpack"]},
        items,
        complete_scan=True,
    )

    result = make_optimizer(catalog, valuation).solve(state)

    assert result.status == SolveStatus.OPTIMAL
    assert result.target_value == 440
    assert all(items[index].instance_id in result.selected_instance_ids for index in range(1, 5))
    assert items[0].instance_id not in result.selected_instance_ids


def test_blocked_cells_reduce_capacity(catalog, valuation) -> None:
    backpack = ContainerDefinition(
        "backpack",
        "隔断背包",
        "backpack",
        2,
        2,
        blocked_cells={(1, 1)},
    )
    items = [ItemInstance("small_high") for _ in range(4)]
    state = InventoryState({"backpack": backpack}, items, complete_scan=True)

    result = make_optimizer(catalog, valuation).solve(state)

    assert result.status == SolveStatus.OPTIMAL
    assert len(result.selected_instance_ids) == 3
    assert result.target_value == 330


def test_safe_box_secondary_objective_protects_high_value(catalog, valuation) -> None:
    high = ItemInstance("small_high")
    low = ItemInstance("small_low")
    state = InventoryState(catalog.containers, [high, low], complete_scan=True)

    result = make_optimizer(catalog, valuation).solve(state)

    safe_placement = next(
        placement for placement in result.placements if placement.container_id == "safe_box"
    )
    assert safe_placement.instance_id == high.instance_id
    assert result.protected_value == 110


def test_safe_box_restriction_is_enforced(catalog, valuation) -> None:
    state = InventoryState(
        {"safe_box": catalog.containers["safe_box"]},
        [ItemInstance("unsafe")],
        complete_scan=True,
    )

    result = make_optimizer(catalog, valuation).solve(state)

    assert result.status == SolveStatus.OPTIMAL
    assert result.target_value == 0
    assert not result.placements


def test_attachment_can_be_installed_on_selected_weapon(catalog, valuation) -> None:
    weapon = ItemInstance("weapon")
    optic = ItemInstance("optic")
    state = InventoryState({}, [weapon, optic], complete_scan=True)

    result = make_optimizer(catalog, valuation).solve(state)

    assert result.status == SolveStatus.OPTIMAL
    assert result.target_value == 1300
    assert {weapon.instance_id, optic.instance_id} == result.selected_instance_ids
    assert any(
        placement.container_id.startswith(f"weapon:{weapon.instance_id}:optic")
        for placement in result.placements
    )


def test_partial_ammo_stacks_can_share_one_cell(catalog, valuation) -> None:
    backpack = ContainerDefinition("backpack", "单格背包", "backpack", 1, 1)
    first = ItemInstance("ammo", quantity=25)
    second = ItemInstance("ammo", quantity=35)
    state = InventoryState(
        {"backpack": backpack},
        [first, second],
        complete_scan=True,
    )

    result = make_optimizer(catalog, valuation).solve(state)

    assert result.status == SolveStatus.OPTIMAL
    assert result.target_value == 6000
    assert len(result.selected_instance_ids) == 2
    assert {(placement.x, placement.y) for placement in result.placements} == {(0, 0)}
    assert any(step.action_type.value == "merge" for step in result.actions)


def test_matching_ammo_can_be_loaded_into_magazine(catalog, valuation) -> None:
    magazine_definition = ItemDefinition(
        id="magazine",
        name="测试弹匣",
        aliases=[],
        category="magazine",
        width=1,
        height=2,
        attachment_type="magazine",
        compatible_weapons={"weapon"},
        caliber="test",
        ammo_capacity=60,
        allowed_containers={"backpack", "weapon_attachment"},
    )
    catalog.items["magazine"] = magazine_definition
    catalog.items["weapon"].attachment_slots["magazine"] = 1
    weapon = ItemInstance("weapon")
    magazine = ItemInstance("magazine")
    ammo = ItemInstance("ammo", quantity=60)
    state = InventoryState({}, [weapon, magazine, ammo], complete_scan=True)

    result = make_optimizer(catalog, valuation).solve(state)

    assert result.status == SolveStatus.OPTIMAL
    assert result.target_value == 7000
    assert {weapon.instance_id, magazine.instance_id, ammo.instance_id} == (
        result.selected_instance_ids
    )
    assert any(
        placement.container_id == f"magazine:{magazine.instance_id}"
        for placement in result.placements
    )


def test_replacing_backpack_enables_its_own_storage(catalog, valuation) -> None:
    old_bag_definition = ItemDefinition(
        id="old_bag",
        name="旧背包",
        aliases=[],
        category="backpack",
        width=2,
        height=2,
        equipment_slots={"backpack"},
        allowed_containers={"equipment_slot"},
    )
    new_bag_definition = ItemDefinition(
        id="new_bag",
        name="新背包",
        aliases=[],
        category="backpack",
        width=2,
        height=2,
        equipment_slots={"backpack"},
        allowed_containers={"equipment_slot"},
    )
    catalog.items["old_bag"] = old_bag_definition
    catalog.items["new_bag"] = new_bag_definition
    old_bag = ItemInstance("old_bag", origin=Origin.CARRIED)
    new_bag = ItemInstance("new_bag", origin=Origin.LOOT)
    old_container = ContainerDefinition(
        "old_contents",
        "旧背包空间",
        "backpack",
        1,
        1,
        source_item_instance_id=old_bag.instance_id,
    )
    new_container = ContainerDefinition(
        "new_contents",
        "新背包空间",
        "backpack",
        2,
        1,
        source_item_instance_id=new_bag.instance_id,
    )
    loot = [
        ItemInstance("small_high"),
        ItemInstance("small_high"),
    ]
    state = InventoryState(
        {
            old_container.id: old_container,
            new_container.id: new_container,
        },
        [old_bag, new_bag, *loot],
        complete_scan=True,
    )

    result = make_optimizer(catalog, valuation).solve(state)

    assert result.status == SolveStatus.OPTIMAL
    assert new_bag.instance_id in result.selected_instance_ids
    assert old_bag.instance_id not in result.selected_instance_ids
    assert all(item.instance_id in result.selected_instance_ids for item in loot)
    assert {
        placement.container_id
        for placement in result.placements
        if placement.instance_id in {item.instance_id for item in loot}
    } == {"new_contents"}


def test_unresolved_items_block_global_optimum(catalog, valuation) -> None:
    state = InventoryState(
        catalog.containers,
        [],
        unresolved=[UnresolvedRecognition("u1", 0.2, [])],
        complete_scan=True,
    )

    result = make_optimizer(catalog, valuation).solve(state)

    assert result.status == SolveStatus.BLOCKED


def test_impossible_locked_combination_is_infeasible(catalog, valuation) -> None:
    state = InventoryState(
        {"backpack": catalog.containers["backpack"]},
        [ItemInstance("large", locked=True), ItemInstance("large", locked=True)],
        complete_scan=True,
    )

    result = make_optimizer(catalog, valuation).solve(state)

    assert result.status == SolveStatus.INFEASIBLE


def test_current_value_and_net_gain(catalog, valuation) -> None:
    carried = ItemInstance(
        "small_low",
        origin=Origin.CARRIED,
        source_container_id="backpack",
    )
    loot = ItemInstance("small_high")
    state = InventoryState(catalog.containers, [carried, loot], complete_scan=True)

    result = make_optimizer(catalog, valuation).solve(state)

    assert result.current_value == 10
    assert result.target_value == 120
    assert result.net_gain == 110


def test_small_random_cases_match_brute_force(catalog, valuation) -> None:
    optimizer = make_optimizer(catalog, valuation)
    single_row = ContainerDefinition("backpack", "三格背包", "backpack", 3, 1)
    rng = random.Random(20260723)

    for _ in range(12):
        items = [
            ItemInstance(
                "small_low",
                manual_unit_price=rng.randint(1, 200),
            )
            for _ in range(6)
        ]
        state = InventoryState({"backpack": single_row}, items, complete_scan=True)
        result = optimizer.solve(state)
        brute_force = max(
            sum(items[index].manual_unit_price for index in choice)
            for size in range(4)
            for choice in itertools.combinations(range(len(items)), size)
        )
        assert result.status == SolveStatus.OPTIMAL
        assert result.target_value == brute_force
