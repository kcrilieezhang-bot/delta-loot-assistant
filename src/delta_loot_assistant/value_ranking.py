from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from .catalog import Catalog
from .models import InventoryState, ItemInstance, Origin
from .pricing import ValuationService


@dataclass(slots=True, frozen=True)
class RankedItemValue:
    instance_id: str
    definition_id: str
    name: str
    source_container_id: str | None
    quantity: int
    occupied_cells: int
    unit_price: int
    total_value: int
    value_per_cell: int
    price_available: bool
    confidence: float
    locked: bool


class InventoryValueRanker:
    """Ranks independently replaceable carried items by their space efficiency."""

    def __init__(self, catalog: Catalog, valuation: ValuationService):
        self.catalog = catalog
        self.valuation = valuation

    def rank_carried_items(self, state: InventoryState) -> list[RankedItemValue]:
        item_index = {item.instance_id: item for item in state.items}
        installed_child_ids = {
            child_id for item in state.items for child_id in item.child_instance_ids
        }
        ranked = [
            self.value_for_item(item, item_index)
            for item in state.items
            if item.origin == Origin.CARRIED
            and item.instance_id not in installed_child_ids
            and item.installed_on is None
        ]
        return sorted(
            ranked,
            key=lambda row: (
                not row.price_available,
                row.value_per_cell,
                row.total_value,
                row.name,
                row.instance_id,
            ),
        )

    def lowest_replaceable(self, state: InventoryState) -> RankedItemValue | None:
        if state.unresolved or not state.complete_scan:
            return None
        if any(
            item.origin == Origin.CARRIED
            and not item.locked
            and (
                item.metadata.get("weapon_details_pending")
                or item.metadata.get("review_required")
                or (
                    item.manual_unit_price is None
                    and self.valuation.provider.unit_price(item.definition_id) is None
                )
            )
            for item in state.items
        ):
            return None
        return next(
            (
                row
                for row in self.rank_carried_items(state)
                if not row.locked and row.price_available
            ),
            None,
        )

    def value_for_item(
        self,
        item: ItemInstance,
        item_index: dict[str, ItemInstance],
    ) -> RankedItemValue:
        definition = self.catalog.items[item.definition_id]
        price_entry = self.valuation.provider.unit_price(item.definition_id)
        unit_price = (
            item.manual_unit_price
            if item.manual_unit_price is not None
            else price_entry.unit_price if price_entry is not None else 0
        )
        price_available = item.manual_unit_price is not None or price_entry is not None
        stack_count = max(1, ceil(item.quantity / definition.stack_limit))
        occupied_cells = max(1, definition.width * definition.height * stack_count)
        total_value = self.valuation.item_value(item, item_index=item_index)
        return RankedItemValue(
            instance_id=item.instance_id,
            definition_id=item.definition_id,
            name=definition.name,
            source_container_id=item.source_container_id,
            quantity=item.quantity,
            occupied_cells=occupied_cells,
            unit_price=unit_price,
            total_value=total_value,
            value_per_cell=round(total_value / occupied_cells),
            price_available=price_available,
            confidence=item.confidence,
            locked=item.locked,
        )
