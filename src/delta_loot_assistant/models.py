from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class Origin(StrEnum):
    CARRIED = "carried"
    LOOT = "loot"


class SolveStatus(StrEnum):
    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    BLOCKED = "blocked"
    ERROR = "error"


class ActionType(StrEnum):
    TAKE = "take"
    DROP = "drop"
    MOVE = "move"
    LOCK = "lock"
    MERGE = "merge"
    EQUIP = "equip"
    UNEQUIP = "unequip"
    INSTALL = "install"
    REMOVE = "remove"
    SWAP = "swap"


@dataclass(slots=True, frozen=True)
class Cell:
    x: int
    y: int


@dataclass(slots=True)
class ContainerDefinition:
    id: str
    name: str
    kind: str
    width: int
    height: int
    blocked_cells: set[tuple[int, int]] = field(default_factory=set)
    enabled: bool = True
    source_item_instance_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContainerDefinition:
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", data["id"])),
            kind=str(data.get("kind", "backpack")),
            width=int(data["width"]),
            height=int(data["height"]),
            blocked_cells={tuple(map(int, cell)) for cell in data.get("blocked_cells", [])},
            enabled=bool(data.get("enabled", True)),
            source_item_instance_id=data.get("source_item_instance_id"),
        )

    def usable_cells(self) -> set[tuple[int, int]]:
        return {
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if (x, y) not in self.blocked_cells
        }


@dataclass(slots=True)
class ItemDefinition:
    id: str
    name: str
    aliases: list[str]
    category: str
    width: int
    height: int
    grade: int = 1
    stack_limit: int = 1
    weight: float = 0.0
    allowed_containers: set[str] = field(default_factory=lambda: {"backpack"})
    safe_box_allowed: bool = False
    fixed_orientation: bool = True
    weapon_slots: set[str] = field(default_factory=set)
    equipment_slots: set[str] = field(default_factory=set)
    attachment_slots: dict[str, int] = field(default_factory=dict)
    attachment_type: str | None = None
    compatible_weapons: set[str] = field(default_factory=set)
    caliber: str | None = None
    ammo_capacity: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ItemDefinition:
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            aliases=[str(alias) for alias in data.get("aliases", [])],
            category=str(data.get("category", "collectible")),
            width=int(data.get("width", 1)),
            height=int(data.get("height", 1)),
            grade=int(data.get("grade", 1)),
            stack_limit=max(1, int(data.get("stack_limit", 1))),
            weight=float(data.get("weight", 0.0)),
            allowed_containers={str(value) for value in data.get("allowed_containers", [])}
            or {"backpack"},
            safe_box_allowed=bool(data.get("safe_box_allowed", False)),
            fixed_orientation=bool(data.get("fixed_orientation", True)),
            weapon_slots={str(value) for value in data.get("weapon_slots", [])},
            equipment_slots={
                str(value) for value in data.get("equipment_slots", [])
            },
            attachment_slots={
                str(key): int(value) for key, value in data.get("attachment_slots", {}).items()
            },
            attachment_type=data.get("attachment_type"),
            compatible_weapons={
                str(value) for value in data.get("compatible_weapons", [])
            },
            caliber=data.get("caliber"),
            ammo_capacity=max(0, int(data.get("ammo_capacity", 0))),
        )

    def orientations(self) -> tuple[tuple[int, int], ...]:
        if self.fixed_orientation or self.width == self.height:
            return ((self.width, self.height),)
        return ((self.width, self.height), (self.height, self.width))


@dataclass(slots=True)
class ItemInstance:
    definition_id: str
    instance_id: str = field(default_factory=lambda: uuid4().hex)
    origin: Origin = Origin.LOOT
    quantity: int = 1
    durability: float = 1.0
    sellable: bool = True
    locked: bool = False
    confidence: float = 1.0
    source_capture_id: str | None = None
    source_container_id: str | None = None
    source_x: int | None = None
    source_y: int | None = None
    installed_on: str | None = None
    installed_slot: str | None = None
    child_instance_ids: list[str] = field(default_factory=list)
    manual_unit_price: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ItemInstance:
        return cls(
            definition_id=str(data["definition_id"]),
            instance_id=str(data.get("instance_id") or uuid4().hex),
            origin=Origin(data.get("origin", Origin.LOOT.value)),
            quantity=max(1, int(data.get("quantity", 1))),
            durability=max(0.0, min(1.0, float(data.get("durability", 1.0)))),
            sellable=bool(data.get("sellable", True)),
            locked=bool(data.get("locked", False)),
            confidence=max(0.0, min(1.0, float(data.get("confidence", 1.0)))),
            source_capture_id=data.get("source_capture_id"),
            source_container_id=data.get("source_container_id"),
            source_x=data.get("source_x"),
            source_y=data.get("source_y"),
            installed_on=data.get("installed_on"),
            installed_slot=data.get("installed_slot"),
            child_instance_ids=list(data.get("child_instance_ids", [])),
            manual_unit_price=data.get("manual_unit_price"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class UnresolvedRecognition:
    recognition_id: str
    confidence: float
    candidate_definition_ids: list[str]
    crop_path: str | None = None
    source_capture_id: str | None = None
    reason: str = "低置信度物品"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InventoryState:
    containers: dict[str, ContainerDefinition]
    items: list[ItemInstance]
    unresolved: list[UnresolvedRecognition] = field(default_factory=list)
    capture_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    complete_scan: bool = False

    def item_by_id(self, instance_id: str) -> ItemInstance | None:
        return next((item for item in self.items if item.instance_id == instance_id), None)


@dataclass(slots=True)
class PriceEntry:
    unit_price: int
    durability_factor: float = 1.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | int | float) -> PriceEntry:
        if isinstance(data, int | float):
            return cls(unit_price=max(0, int(data)))
        return cls(
            unit_price=max(0, int(data.get("unit_price", 0))),
            durability_factor=max(0.0, float(data.get("durability_factor", 1.0))),
        )


@dataclass(slots=True)
class PricePack:
    schema_version: int
    region: str
    season: str
    generated_at: datetime
    source: str
    prices: dict[str, PriceEntry]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PricePack:
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            region=str(data.get("region", "cn")),
            season=str(data.get("season", "unknown")),
            generated_at=datetime.fromisoformat(str(data["generated_at"])),
            source=str(data.get("source", "unknown")),
            prices={
                str(item_id): PriceEntry.from_dict(entry)
                for item_id, entry in data.get("prices", {}).items()
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "region": self.region,
            "season": self.season,
            "generated_at": self.generated_at.isoformat(),
            "source": self.source,
            "prices": {
                item_id: asdict(entry) for item_id, entry in self.prices.items()
            },
        }


@dataclass(slots=True)
class Placement:
    instance_id: str
    container_id: str
    x: int
    y: int
    width: int
    height: int


@dataclass(slots=True)
class ActionStep:
    index: int
    action_type: ActionType
    item_instance_id: str
    instruction: str
    source_container_id: str | None = None
    target_container_id: str | None = None
    value_delta: int = 0


@dataclass(slots=True)
class PlanResult:
    status: SolveStatus
    current_value: int
    target_value: int
    net_gain: int
    protected_value: int
    placements: list[Placement]
    selected_instance_ids: set[str]
    actions: list[ActionStep]
    warnings: list[str] = field(default_factory=list)
    solve_seconds: float = 0.0

    @property
    def headline(self) -> str:
        if self.status == SolveStatus.OPTIMAL:
            return "最高收益"
        if self.status == SolveStatus.FEASIBLE:
            return "当前最佳方案，尚未证明全局最优"
        if self.status == SolveStatus.BLOCKED:
            return "需要确认识别结果"
        if self.status == SolveStatus.INFEASIBLE:
            return "没有满足锁定条件的合法方案"
        return "计算失败"
