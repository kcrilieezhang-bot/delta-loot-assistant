from __future__ import annotations

from datetime import UTC, datetime, timedelta

from delta_loot_assistant.models import (
    InventoryState,
    ItemInstance,
    Origin,
    PriceEntry,
    PricePack,
)
from delta_loot_assistant.pricing import LocalPricePackProvider, ValuationService


def test_quantity_and_durability_valuation(valuation) -> None:
    ammo = ItemInstance("ammo", quantity=30)
    armor = ItemInstance("armor", durability=0.5)

    assert valuation.item_value(ammo) == 3000
    assert valuation.item_value(armor) == 750


def test_composite_weapon_is_not_double_counted(catalog, valuation) -> None:
    optic = ItemInstance(
        "optic",
        origin=Origin.CARRIED,
        installed_on="gun",
        instance_id="scope",
    )
    weapon = ItemInstance(
        "weapon",
        origin=Origin.CARRIED,
        instance_id="gun",
        child_instance_ids=["scope"],
    )
    state = InventoryState(catalog.containers, [weapon, optic], complete_scan=True)

    assert valuation.item_value(weapon, {"gun": weapon, "scope": optic}) == 1300
    assert valuation.state_current_value(state) == 1300


def test_nested_composite_includes_loaded_ammo_once(catalog, valuation) -> None:
    ammo = ItemInstance(
        "ammo",
        origin=Origin.CARRIED,
        quantity=2,
        installed_on="scope",
        instance_id="rounds",
    )
    optic = ItemInstance(
        "optic",
        origin=Origin.CARRIED,
        installed_on="gun",
        instance_id="scope",
        child_instance_ids=["rounds"],
    )
    weapon = ItemInstance(
        "weapon",
        origin=Origin.CARRIED,
        instance_id="gun",
        child_instance_ids=["scope"],
    )
    state = InventoryState(
        catalog.containers,
        [weapon, optic, ammo],
        complete_scan=True,
    )

    assert valuation.state_current_value(state) == 1500


def test_unsellable_item_has_zero_value(valuation) -> None:
    assert valuation.item_value(ItemInstance("large", sellable=False)) == 0


def test_price_pack_rollback_and_stale_warning(tmp_path) -> None:
    provider = LocalPricePackProvider(tmp_path / "prices.sqlite3")
    first = PricePack(
        1,
        "cn",
        "s1",
        datetime.now(UTC) - timedelta(days=2),
        "first",
        {"x": PriceEntry(10)},
    )
    second = PricePack(
        1,
        "cn",
        "s2",
        datetime.now(UTC),
        "second",
        {"x": PriceEntry(20)},
    )
    provider.save_pack(first)
    provider.save_pack(second)

    assert provider.unit_price("x").unit_price == 20
    assert provider.rollback().source == "first"
    assert provider.unit_price("x").unit_price == 10
    assert "24" in ValuationService(provider).stale_warning()

    third = PricePack(
        1,
        "cn",
        "s3",
        datetime.now(UTC),
        "third",
        {"x": PriceEntry(30)},
    )
    provider.save_pack(third)
    assert provider.rollback().source == "first"
