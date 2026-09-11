from __future__ import annotations

from collections import defaultdict

from .catalog import Catalog
from .models import (
    ActionStep,
    ActionType,
    InventoryState,
    ItemInstance,
    Placement,
)


class ActionPlanner:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def build(
        self,
        state: InventoryState,
        placements: list[Placement],
        selected_instance_ids: set[str],
        values: dict[str, int],
    ) -> list[ActionStep]:
        placement_by_item = {placement.instance_id: placement for placement in placements}
        steps: list[ActionStep] = []
        item_by_id = {item.instance_id: item for item in state.items}
        child_ids = {
            child_id for item in state.items for child_id in item.child_instance_ids
        }

        # 先丢弃最低价值的随身物品，为后续交换制造缓冲区。
        drops = [
            item
            for item in state.items
            if item.origin.value == "carried"
            and item.instance_id not in selected_instance_ids
            and item.instance_id not in child_ids
        ]
        drops.sort(key=lambda item: values.get(item.instance_id, 0))
        for item in drops:
            definition = self.catalog.items.get(item.definition_id)
            steps.append(
                ActionStep(
                    index=0,
                    action_type=ActionType.DROP,
                    item_instance_id=item.instance_id,
                    instruction=f"丢弃 {definition.name if definition else item.definition_id}",
                    source_container_id=item.source_container_id,
                    value_delta=-values.get(item.instance_id, 0),
                )
            )

        # 再处理拆件。若整枪未选择、子配件被选择，必须先拆卸。
        for parent in state.items:
            if parent.instance_id in selected_instance_ids:
                continue
            for child_id in parent.child_instance_ids:
                if child_id not in selected_instance_ids or child_id not in item_by_id:
                    continue
                child = item_by_id[child_id]
                child_def = self.catalog.items.get(child.definition_id)
                parent_def = self.catalog.items.get(parent.definition_id)
                steps.append(
                    ActionStep(
                        index=0,
                        action_type=ActionType.REMOVE,
                        item_instance_id=child_id,
                        instruction=(
                            f"从 {parent_def.name if parent_def else parent.definition_id} "
                            f"拆下 {child_def.name if child_def else child.definition_id}"
                        ),
                        source_container_id=parent.source_container_id,
                        value_delta=values.get(child_id, 0),
                    )
                )

        takes_and_moves: list[tuple[int, ActionStep]] = []
        for item in state.items:
            if item.instance_id not in selected_instance_ids:
                continue
            placement = placement_by_item.get(item.instance_id)
            if placement is None:
                continue
            definition = self.catalog.items.get(item.definition_id)
            name = definition.name if definition else item.definition_id
            target_name = self._container_name(
                placement.container_id,
                item_by_id,
                state.containers,
            )
            if placement.container_id.startswith("weapon:"):
                action = ActionType.INSTALL
                instruction = f"将 {name} 安装到 {target_name}"
            elif placement.container_id.startswith("magazine:"):
                action = ActionType.MERGE
                instruction = f"将 {item.quantity} 发 {name} 装入 {target_name}"
            elif placement.container_id.startswith("equipment_slot_"):
                action = ActionType.EQUIP
                instruction = f"装备 {name} 到 {target_name}"
            elif item.origin.value == "loot":
                action = ActionType.TAKE
                instruction = (
                    f"拿取 {name}，放入 {target_name} "
                    f"({placement.x + 1},{placement.y + 1})"
                )
            elif item.source_container_id != placement.container_id:
                action = ActionType.MOVE
                instruction = (
                    f"移动 {name} 到 {target_name} "
                    f"({placement.x + 1},{placement.y + 1})"
                )
            else:
                continue
            step = ActionStep(
                index=0,
                action_type=action,
                item_instance_id=item.instance_id,
                instruction=instruction,
                source_container_id=item.source_container_id,
                target_container_id=placement.container_id,
                value_delta=values.get(item.instance_id, 0)
                if item.origin.value == "loot"
                else 0,
            )
            # 高价值拿取优先，减少被中断时的损失。
            takes_and_moves.append((values.get(item.instance_id, 0), step))

        takes_and_moves.sort(key=lambda pair: -pair[0])
        steps.extend(step for _, step in takes_and_moves)

        stack_groups: dict[tuple[str, int, int, str], list[ItemInstance]] = defaultdict(list)
        for placement in placements:
            item = item_by_id[placement.instance_id]
            definition = self.catalog.items[item.definition_id]
            if definition.stack_limit <= 1:
                continue
            stack_groups[
                (
                    placement.container_id,
                    placement.x,
                    placement.y,
                    item.definition_id,
                )
            ].append(item)
        for (container_id, _, _, definition_id), items in stack_groups.items():
            if len(items) <= 1:
                continue
            definition = self.catalog.items[definition_id]
            quantity = sum(item.quantity for item in items)
            steps.append(
                ActionStep(
                    index=0,
                    action_type=ActionType.MERGE,
                    item_instance_id=items[0].instance_id,
                    instruction=f"合并 {definition.name}，共 {quantity} 发/个",
                    target_container_id=container_id,
                )
            )

        self._prepend_temporary_space_step(
            state,
            placements,
            selected_instance_ids,
            values,
            steps,
        )
        for index, step in enumerate(steps, start=1):
            step.index = index
        return steps

    def _container_name(
        self,
        container_id: str,
        item_by_id: dict[str, ItemInstance],
        state_containers,
    ) -> str:
        if container_id.startswith("weapon:"):
            _, weapon_id, slot = container_id.split(":", 2)
            weapon = item_by_id.get(weapon_id)
            weapon_name = (
                self.catalog.items[weapon.definition_id].name
                if weapon and weapon.definition_id in self.catalog.items
                else "目标武器"
            )
            return f"{weapon_name} 的 {slot} 槽位"
        if container_id.startswith("magazine:"):
            magazine_id = container_id.removeprefix("magazine:")
            magazine = item_by_id.get(magazine_id)
            return (
                self.catalog.items[magazine.definition_id].name
                if magazine
                else "目标弹匣"
            )
        if container_id.startswith("weapon_slot_"):
            return f"{container_id.removeprefix('weapon_slot_')}号武器栏"
        if container_id.startswith("equipment_slot_"):
            slot = container_id.removeprefix("equipment_slot_")
            return "背包装备栏" if slot == "backpack" else "胸挂装备栏"
        container = state_containers.get(container_id) or self.catalog.containers.get(
            container_id
        )
        return container.name if container else container_id

    def _prepend_temporary_space_step(
        self,
        state: InventoryState,
        placements: list[Placement],
        selected_instance_ids: set[str],
        values: dict[str, int],
        steps: list[ActionStep],
    ) -> None:
        moves = [
            item
            for item in state.items
            if item.origin.value == "carried"
            and item.instance_id in selected_instance_ids
            and any(
                placement.instance_id == item.instance_id
                and placement.container_id != item.source_container_id
                for placement in placements
            )
        ]
        has_drop = any(step.action_type == ActionType.DROP for step in steps)
        targets = {
            placement.instance_id: placement.container_id
            for placement in placements
        }
        edges = {
            (item.source_container_id, targets[item.instance_id])
            for item in moves
            if item.source_container_id
        }
        if len(moves) < 2 or has_drop or not self._has_container_cycle(edges):
            return

        occupied = {
            (placement.container_id, x, y)
            for placement in placements
            for y in range(placement.y, placement.y + placement.height)
            for x in range(placement.x, placement.x + placement.width)
        }
        free_cell: tuple[str, int, int] | None = None
        for container in state.containers.values():
            for y in range(container.height):
                for x in range(container.width):
                    if (
                        (x, y) not in container.blocked_cells
                        and (container.id, x, y) not in occupied
                    ):
                        free_cell = (container.id, x, y)
                        break
                if free_cell:
                    break
            if free_cell:
                break

        temporary_item = min(moves, key=lambda item: values.get(item.instance_id, 0))
        definition = self.catalog.items[temporary_item.definition_id]
        if free_cell:
            container_id, x, y = free_cell
            container_name = state.containers[container_id].name
            instruction = (
                f"交换前先把 {definition.name} 临时放到 {container_name} "
                f"({x + 1},{y + 1})"
            )
        else:
            instruction = (
                f"没有临时空格：先把 {definition.name} 临时放回敌方物资区，"
                "完成交换后按后续步骤拿回"
            )
        steps.insert(
            0,
            ActionStep(
                index=0,
                action_type=ActionType.SWAP,
                item_instance_id=temporary_item.instance_id,
                instruction=instruction,
                source_container_id=temporary_item.source_container_id,
                target_container_id=free_cell[0] if free_cell else None,
            ),
        )

    @staticmethod
    def _has_container_cycle(edges: set[tuple[str, str]]) -> bool:
        graph: dict[str, set[str]] = defaultdict(set)
        for source, target in edges:
            graph[source].add(target)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            if any(visit(target) for target in graph.get(node, ())):
                return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(visit(node) for node in graph)
