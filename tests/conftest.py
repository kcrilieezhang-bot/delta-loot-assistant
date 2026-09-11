from __future__ import annotations

from datetime import UTC, datetime

import pytest

from delta_loot_assistant.catalog import Catalog
from delta_loot_assistant.models import (
    ContainerDefinition,
    ItemDefinition,
    PriceEntry,
    PricePack,
)
from delta_loot_assistant.pricing import LocalPricePackProvider, ValuationService


@pytest.fixture
def catalog() -> Catalog:
    definitions = [
        ItemDefinition(
            id="small_high",
            name="高价值小物品",
            aliases=[],
            category="collectible",
            width=1,
            height=1,
            weight=0.2,
            allowed_containers={"backpack", "safe_box"},
            safe_box_allowed=True,
        ),
        ItemDefinition(
            id="small_low",
            name="低价值小物品",
            aliases=[],
            category="collectible",
            width=1,
            height=1,
            weight=0.1,
            allowed_containers={"backpack", "safe_box"},
            safe_box_allowed=True,
        ),
        ItemDefinition(
            id="large",
            name="大型物品",
            aliases=[],
            category="collectible",
            width=2,
            height=2,
            weight=2.0,
            allowed_containers={"backpack"},
        ),
        ItemDefinition(
            id="ammo",
            name="弹药",
            aliases=[],
            category="ammo",
            width=1,
            height=1,
            stack_limit=60,
            weight=0.01,
            allowed_containers={"backpack", "safe_box"},
            safe_box_allowed=True,
            caliber="test",
        ),
        ItemDefinition(
            id="armor",
            name="护甲",
            aliases=[],
            category="armor",
            width=2,
            height=2,
            allowed_containers={"backpack"},
        ),
        ItemDefinition(
            id="weapon",
            name="测试步枪",
            aliases=[],
            category="weapon",
            width=2,
            height=2,
            weight=3.0,
            allowed_containers={"backpack", "weapon_slot"},
            weapon_slots={"primary", "secondary"},
            attachment_slots={"optic": 1},
        ),
        ItemDefinition(
            id="optic",
            name="测试瞄具",
            aliases=[],
            category="attachment",
            width=1,
            height=1,
            weight=0.2,
            allowed_containers={"backpack", "safe_box", "weapon_attachment"},
            safe_box_allowed=True,
            attachment_type="optic",
            compatible_weapons={"weapon"},
        ),
        ItemDefinition(
            id="unsafe",
            name="不可放安全箱",
            aliases=[],
            category="collectible",
            width=1,
            height=1,
            allowed_containers={"backpack"},
            safe_box_allowed=False,
        ),
    ]
    containers = [
        ContainerDefinition("backpack", "背包", "backpack", 2, 2),
        ContainerDefinition("safe_box", "安全箱", "safe_box", 1, 1),
    ]
    return Catalog(
        schema_version=1,
        season="test-season",
        items={item.id: item for item in definitions},
        containers={container.id: container for container in containers},
    )


@pytest.fixture
def price_provider(tmp_path) -> LocalPricePackProvider:
    provider = LocalPricePackProvider(tmp_path / "prices.sqlite3")
    provider.save_pack(
        PricePack(
            schema_version=1,
            region="cn",
            season="test-season",
            generated_at=datetime.now(UTC),
            source="test",
            prices={
                "small_high": PriceEntry(110),
                "small_low": PriceEntry(10),
                "large": PriceEntry(400),
                "ammo": PriceEntry(100),
                "armor": PriceEntry(1000, durability_factor=0.5),
                "weapon": PriceEntry(1000),
                "optic": PriceEntry(300),
                "unsafe": PriceEntry(500),
            },
        )
    )
    return provider


@pytest.fixture
def valuation(price_provider) -> ValuationService:
    return ValuationService(price_provider)
