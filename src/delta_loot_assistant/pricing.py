from __future__ import annotations

import csv
import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path

from .models import InventoryState, ItemInstance, PriceEntry, PricePack


class PriceProvider(ABC):
    @abstractmethod
    def current_pack(self) -> PricePack | None:
        raise NotImplementedError

    @abstractmethod
    def unit_price(self, item_id: str) -> PriceEntry | None:
        raise NotImplementedError


class LocalPricePackProvider(PriceProvider):
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS price_packs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    imported_at TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    region TEXT NOT NULL,
                    season TEXT NOT NULL,
                    source TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    previous_pack_id INTEGER,
                    active INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_price_packs_active
                    ON price_packs(active, id DESC);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(price_packs)"
                ).fetchall()
            }
            if "previous_pack_id" not in columns:
                connection.execute(
                    "ALTER TABLE price_packs ADD COLUMN previous_pack_id INTEGER"
                )

    def import_pack(self, path: str | Path) -> PricePack:
        source_path = Path(path)
        if source_path.suffix.lower() == ".csv":
            pack = self._read_csv(source_path)
        else:
            pack = PricePack.from_dict(json.loads(source_path.read_text(encoding="utf-8")))
        self.save_pack(pack)
        return pack

    def save_pack(self, pack: PricePack) -> None:
        payload = json.dumps(pack.to_dict(), ensure_ascii=False)
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT id FROM price_packs WHERE active = 1 LIMIT 1"
            ).fetchone()
            previous_id = int(previous["id"]) if previous else None
            connection.execute("UPDATE price_packs SET active = 0 WHERE active = 1")
            connection.execute(
                """
                INSERT INTO price_packs (
                    imported_at, generated_at, region, season, source,
                    schema_version, payload, previous_pack_id, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    pack.generated_at.isoformat(),
                    pack.region,
                    pack.season,
                    pack.source,
                    pack.schema_version,
                    payload,
                    previous_id,
                ),
            )

    def rollback(self) -> PricePack | None:
        with self._connect() as connection:
            current = connection.execute(
                """
                SELECT id, previous_pack_id
                FROM price_packs
                WHERE active = 1
                LIMIT 1
                """
            ).fetchone()
            if current is None or current["previous_pack_id"] is None:
                return None
            target_id = int(current["previous_pack_id"])
            connection.execute("UPDATE price_packs SET active = 0")
            connection.execute(
                "UPDATE price_packs SET active = 1 WHERE id = ?", (target_id,)
            )
        return self.current_pack()

    def current_pack(self) -> PricePack | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM price_packs WHERE active = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return PricePack.from_dict(json.loads(row["payload"])) if row else None

    def unit_price(self, item_id: str) -> PriceEntry | None:
        pack = self.current_pack()
        return pack.prices.get(item_id) if pack else None

    @staticmethod
    def _read_csv(path: Path) -> PricePack:
        prices: dict[str, PriceEntry] = {}
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                item_id = str(row.get("item_id", "")).strip()
                if not item_id:
                    continue
                prices[item_id] = PriceEntry(
                    unit_price=max(0, int(float(row.get("unit_price", 0) or 0))),
                    durability_factor=max(
                        0.0, float(row.get("durability_factor", 1) or 1)
                    ),
                )
        return PricePack(
            schema_version=1,
            region="cn",
            season="imported-csv",
            generated_at=datetime.now(UTC),
            source=str(path),
            prices=prices,
        )


class ValuationService:
    def __init__(self, provider: PriceProvider):
        self.provider = provider

    def item_value(
        self,
        item: ItemInstance,
        item_index: dict[str, ItemInstance] | None = None,
        _seen: set[str] | None = None,
    ) -> int:
        seen = set() if _seen is None else set(_seen)
        if item.instance_id in seen:
            return 0
        seen.add(item.instance_id)
        entry = (
            PriceEntry(item.manual_unit_price)
            if item.manual_unit_price is not None
            else self.provider.unit_price(item.definition_id)
        )
        own_value = 0
        if item.sellable and entry is not None:
            durability_multiplier = 1.0 - (
                (1.0 - item.durability) * entry.durability_factor
            )
            own_value = round(
                entry.unit_price
                * item.quantity
                * max(0.0, durability_multiplier)
            )
        if not item_index:
            return own_value
        child_value = sum(
            self.item_value(item_index[child_id], item_index=item_index, _seen=seen)
            for child_id in item.child_instance_ids
            if child_id in item_index
        )
        return own_value + child_value

    def state_current_value(self, state: InventoryState) -> int:
        index = {item.instance_id: item for item in state.items}
        child_ids = {
            child_id for item in state.items for child_id in item.child_instance_ids
        }
        return sum(
            self.item_value(item, index)
            for item in state.items
            if item.origin.value == "carried" and item.instance_id not in child_ids
        )

    def stale_warning(self, expected_season: str | None = None) -> str | None:
        pack = self.provider.current_pack()
        if pack is None:
            return "尚未导入价格包"
        now = datetime.now(pack.generated_at.tzinfo or UTC)
        age_hours = (now - pack.generated_at).total_seconds() / 3600
        if expected_season and pack.season not in {expected_season, "demo"}:
            return f"价格包赛季为 {pack.season}，当前物品库为 {expected_season}"
        if age_hours > 24:
            return f"价格包已超过24小时（{age_hours:.0f}小时），价格可能过期"
        return None
