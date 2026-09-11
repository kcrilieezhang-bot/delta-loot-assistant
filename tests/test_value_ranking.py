from __future__ import annotations

from delta_loot_assistant.models import InventoryState, ItemDefinition, ItemInstance, Origin
from delta_loot_assistant.value_ranking import InventoryValueRanker


def test_carried_items_are_ranked_by_value_per_cell(catalog, valuation) -> None:
    state = InventoryState(
        catalog.containers,
        [
            ItemInstance(
                "small_high",
                instance_id="small-item",
                origin=Origin.CARRIED,
                source_container_id="backpack",
            ),
            ItemInstance(
                "large",
                instance_id="large-item",
                origin=Origin.CARRIED,
                source_container_id="backpack",
            ),
            ItemInstance("small_low", instance_id="loot", origin=Origin.LOOT),
        ],
        complete_scan=True,
    )

    ranked = InventoryValueRanker(catalog, valuation).rank_carried_items(state)

    assert [row.instance_id for row in ranked] == ["large-item", "small-item"]
    assert ranked[0].occupied_cells == 4
    assert ranked[0].total_value == 400
    assert ranked[0].value_per_cell == 100


def test_lowest_replaceable_skips_locked_and_installed_children(
    catalog, valuation
) -> None:
    installed = ItemInstance(
        "optic",
        instance_id="optic-item",
        origin=Origin.CARRIED,
        installed_on="weapon-item",
    )
    weapon = ItemInstance(
        "weapon",
        instance_id="weapon-item",
        origin=Origin.CARRIED,
        child_instance_ids=[installed.instance_id],
        locked=True,
    )
    replaceable = ItemInstance(
        "small_low",
        instance_id="replaceable",
        origin=Origin.CARRIED,
    )
    state = InventoryState(
        catalog.containers,
        [installed, weapon, replaceable],
        complete_scan=True,
    )

    ranker = InventoryValueRanker(catalog, valuation)

    assert [row.instance_id for row in ranker.rank_carried_items(state)] == [
        "replaceable",
        "weapon-item",
    ]
    assert ranker.lowest_replaceable(state).instance_id == "replaceable"


def test_missing_price_is_not_reported_as_cheapest(catalog, valuation) -> None:
    catalog.items["unpriced"] = ItemDefinition(
        id="unpriced",
        name="未定价物品",
        aliases=[],
        category="collectible",
        width=1,
        height=1,
    )
    state = InventoryState(
        catalog.containers,
        [
            ItemInstance("unpriced", instance_id="unknown", origin=Origin.CARRIED),
            ItemInstance("small_low", instance_id="known", origin=Origin.CARRIED),
        ],
    )

    ranker = InventoryValueRanker(catalog, valuation)
    ranked = ranker.rank_carried_items(state)

    assert [row.instance_id for row in ranked] == ["known", "unknown"]
    assert ranked[1].price_available is False
    assert ranker.lowest_replaceable(state) is None
