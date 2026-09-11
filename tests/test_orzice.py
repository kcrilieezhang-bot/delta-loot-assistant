from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from delta_loot_assistant.orzice import (
    OrziceCatalogRepository,
    OrziceDailySyncService,
    SyncDeferredError,
    item_definition_from_row,
)
from delta_loot_assistant.pricing import LocalPricePackProvider


class FakeOrziceClient:
    def __init__(self) -> None:
        self.info_calls = 0
        self.price_calls = 0

    def item_info_all(self):
        self.info_calls += 1
        return [
            {
                "id": 12,
                "oid": 34,
                "objectID": 5678,
                "objectName": "测试弹药",
                "primaryClass": "ammo",
                "secondClass": "5.56x45mm",
                "secondClassCN": "ammo5.56x45",
                "width": 1,
                "length": 1,
                "grade": 4,
                "is_get": 1,
                "pic": "https://example.invalid/item.png",
            }
        ]

    def item_price_all(self):
        self.price_calls += 1
        return [
            {
                "id": 34,
                "tid": 12,
                "price": 1234,
                "is_get_time": 1_784_800_000,
            }
        ]


def test_item_definition_maps_provider_fields() -> None:
    definition = item_definition_from_row(FakeOrziceClient().item_info_all()[0])

    assert definition.id == "df:5678:source:34"
    assert definition.name == "测试弹药"
    assert definition.category == "ammo"
    assert definition.stack_limit == 60
    assert definition.caliber == "5.56x45mm"
    assert definition.fixed_orientation is False


def test_provider_length_maps_to_horizontal_grid_span() -> None:
    row = dict(
        FakeOrziceClient().item_info_all()[0],
        primaryClass="gun",
        width=2,
        length=5,
    )

    definition = item_definition_from_row(row)

    assert (definition.width, definition.height) == (5, 2)
    assert definition.orientations() == ((5, 2), (2, 5))


def test_daily_sync_saves_catalog_and_price_and_blocks_repeat(tmp_path) -> None:
    database_path = tmp_path / "assistant.sqlite3"
    catalog_repository = OrziceCatalogRepository(database_path)
    price_provider = LocalPricePackProvider(database_path)
    client = FakeOrziceClient()
    service = OrziceDailySyncService(
        client,
        catalog_repository,
        price_provider,
        minimum_expected_items=1,
        minimum_expected_prices=1,
    )
    now = datetime(2026, 7, 23, tzinfo=UTC)

    result = service.sync(now=now)

    assert result.item_count == 1
    assert result.priced_item_count == 1
    assert catalog_repository.active_counts() == (1, 1)
    assert catalog_repository.active_definitions()["df:5678:source:34"].name == "测试弹药"
    assert price_provider.unit_price("df:5678:source:34").unit_price == 1234
    assert client.info_calls == 1
    assert client.price_calls == 1

    with pytest.raises(SyncDeferredError):
        service.sync(now=now + timedelta(hours=23))
    assert client.info_calls == 1
    assert client.price_calls == 1


def test_duplicate_official_ids_remain_distinct(tmp_path) -> None:
    database_path = tmp_path / "assistant.sqlite3"
    repository = OrziceCatalogRepository(database_path)
    rows = FakeOrziceClient().item_info_all()
    duplicate = dict(rows[0], id=13, oid=35, objectName="测试弹药变体")

    repository.save_snapshot(
        [*rows, duplicate],
        generated_at=datetime.now(UTC),
        price_count=0,
    )
    definitions = repository.active_definitions()

    assert set(definitions) == {"df:5678:source:34", "df:5678:source:35"}
