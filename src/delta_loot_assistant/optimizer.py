from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass

from ortools.sat.python import cp_model

from .action_planner import ActionPlanner
from .catalog import Catalog
from .models import (
    ContainerDefinition,
    InventoryState,
    ItemInstance,
    Origin,
    Placement,
    PlanResult,
    SolveStatus,
)
from .pricing import ValuationService


@dataclass(slots=True)
class PlacementCandidate:
    item_id: str
    container_id: str
    x: int
    y: int
    width: int
    height: int
    occupied_cells: tuple[tuple[int, int], ...]
    safe: bool = False
    action_cost: int = 1
    required_weapon_id: str | None = None
    attachment_slot: str | None = None
    required_container_item_id: str | None = None
    required_magazine_id: str | None = None


class Optimizer(ABC):
    @abstractmethod
    def solve(self, state: InventoryState) -> PlanResult:
        raise NotImplementedError


class CpSatInventoryOptimizer(Optimizer):
    def __init__(
        self,
        catalog: Catalog,
        valuation: ValuationService,
        max_time_seconds: float = 5.0,
        worker_count: int = 8,
    ):
        self.catalog = catalog
        self.valuation = valuation
        self.max_time_seconds = max(0.2, max_time_seconds)
        self.worker_count = max(1, worker_count)
        self.action_planner = ActionPlanner(catalog)

    def solve(self, state: InventoryState) -> PlanResult:
        started = time.perf_counter()
        current_value = self.valuation.state_current_value(state)
        warnings = list(state.warnings)
        stale = self.valuation.stale_warning(self.catalog.season)
        if stale:
            warnings.append(stale)
        if not state.complete_scan or state.unresolved:
            warnings.append(
                f"仍有 {len(state.unresolved)} 个未确认物品；不能宣称全局最高收益"
            )
            return PlanResult(
                status=SolveStatus.BLOCKED,
                current_value=current_value,
                target_value=current_value,
                net_gain=0,
                protected_value=0,
                placements=[],
                selected_instance_ids=set(),
                actions=[],
                warnings=warnings,
                solve_seconds=time.perf_counter() - started,
            )

        item_by_id = {item.instance_id: item for item in state.items}
        definitions = self.catalog.items
        missing_definitions = sorted(
            {item.definition_id for item in state.items if item.definition_id not in definitions}
        )
        if missing_definitions:
            warnings.append("物品库缺少定义：" + "、".join(missing_definitions))
            return self._error_result(
                SolveStatus.BLOCKED, current_value, warnings, started
            )

        top_level_items = [
            item for item in state.items if item.installed_on is None
        ]
        values = {
            item.instance_id: self.valuation.item_value(item, item_by_id)
            for item in state.items
        }
        containers = {
            key: value for key, value in state.containers.items() if value.enabled
        }
        containers.update(self._virtual_weapon_slots())
        containers.update(self._virtual_equipment_slots())
        candidates = self._build_candidates(
            top_level_items, state.items, containers, item_by_id
        )

        model = cp_model.CpModel()
        selected = {
            item.instance_id: model.new_bool_var(f"selected_{item.instance_id}")
            for item in state.items
        }
        candidate_vars: dict[int, cp_model.IntVar] = {}
        candidates_by_item: dict[str, list[int]] = defaultdict(list)
        cell_candidates: dict[tuple[str, int, int], list[int]] = defaultdict(list)
        virtual_slot_vars: dict[tuple[str, str], list[cp_model.IntVar]] = defaultdict(list)
        magazine_candidate_indexes: dict[str, list[int]] = defaultdict(list)

        for index, candidate in enumerate(candidates):
            variable = model.new_bool_var(f"place_{index}")
            candidate_vars[index] = variable
            candidates_by_item[candidate.item_id].append(index)
            for cell in candidate.occupied_cells:
                cell_candidates[(candidate.container_id, *cell)].append(index)
            if candidate.required_weapon_id and candidate.attachment_slot:
                model.add(variable <= selected[candidate.required_weapon_id])
                virtual_slot_vars[
                    (candidate.required_weapon_id, candidate.attachment_slot)
                ].append(variable)
            if candidate.required_container_item_id:
                required = selected.get(candidate.required_container_item_id)
                if required is None:
                    model.add(variable == 0)
                else:
                    model.add(variable <= required)
            if candidate.required_magazine_id:
                model.add(variable <= selected[candidate.required_magazine_id])
                magazine_candidate_indexes[candidate.required_magazine_id].append(
                    index
                )

        for item in state.items:
            placement_indexes = candidates_by_item.get(item.instance_id, [])
            if item.installed_on:
                # 已安装配件仅在拆下后独立选择；原枪整体选择时由父项隐式携带。
                model.add(
                    sum(candidate_vars[index] for index in placement_indexes)
                    == selected[item.instance_id]
                )
            else:
                model.add(
                    sum(candidate_vars[index] for index in placement_indexes)
                    == selected[item.instance_id]
                )
            if item.locked:
                model.add(selected[item.instance_id] == 1)

        for cell, indexes in cell_candidates.items():
            single_vars: list[cp_model.IntVar] = []
            stack_groups: dict[str, list[int]] = defaultdict(list)
            for index in indexes:
                candidate = candidates[index]
                item = item_by_id[candidate.item_id]
                definition = definitions[item.definition_id]
                if definition.stack_limit > 1:
                    stack_groups[definition.id].append(index)
                else:
                    single_vars.append(candidate_vars[index])
            stack_usage: list[cp_model.IntVar] = []
            for definition_id, group_indexes in stack_groups.items():
                usage = model.new_bool_var(
                    f"stack_{cell[0]}_{cell[1]}_{cell[2]}_{definition_id}"
                )
                group_vars = [candidate_vars[index] for index in group_indexes]
                for variable in group_vars:
                    model.add(variable <= usage)
                model.add(usage <= sum(group_vars))
                model.add(
                    sum(
                        item_by_id[candidates[index].item_id].quantity
                        * candidate_vars[index]
                        for index in group_indexes
                    )
                    <= definitions[definition_id].stack_limit
                )
                stack_usage.append(usage)
            model.add(sum(single_vars) + sum(stack_usage) <= 1)

        for parent in state.items:
            for descendant_id in self._descendant_ids(parent, item_by_id):
                if descendant_id in selected:
                    model.add(
                        selected[parent.instance_id] + selected[descendant_id] <= 1
                    )

        for (weapon_id, slot), variables in virtual_slot_vars.items():
            weapon = item_by_id[weapon_id]
            definition = definitions[weapon.definition_id]
            occupied = sum(
                1
                for child_id in weapon.child_instance_ids
                if child_id in item_by_id
                and definitions[item_by_id[child_id].definition_id].attachment_type == slot
            )
            capacity = max(0, definition.attachment_slots.get(slot, 0) - occupied)
            model.add(sum(variables) <= capacity)

        for magazine_id, indexes in magazine_candidate_indexes.items():
            magazine = item_by_id[magazine_id]
            definition = definitions[magazine.definition_id]
            occupied = sum(
                item_by_id[child_id].quantity
                for child_id in magazine.child_instance_ids
                if child_id in item_by_id
                and definitions[item_by_id[child_id].definition_id].category == "ammo"
            )
            capacity = max(0, definition.ammo_capacity - occupied)
            model.add(
                sum(
                    item_by_id[candidates[index].item_id].quantity
                    * candidate_vars[index]
                    for index in indexes
                )
                <= capacity
            )

        total_value_expr = sum(
            values[item.instance_id] * selected[item.instance_id]
            for item in state.items
        )
        safe_value_expr = sum(
            values[candidate.item_id] * candidate_vars[index]
            for index, candidate in enumerate(candidates)
            if candidate.safe
        )
        action_expr_parts: list[cp_model.LinearExpr] = []
        for index, candidate in enumerate(candidates):
            action_expr_parts.append(candidate.action_cost * candidate_vars[index])
        for item in state.items:
            if item.origin == Origin.CARRIED and item.installed_on is None:
                action_expr_parts.append(1 - selected[item.instance_id])
        action_expr = sum(action_expr_parts)
        weight_expr = sum(
            round(definitions[item.definition_id].weight * item.quantity * 1000)
            * selected[item.instance_id]
            for item in state.items
        )

        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = self.worker_count
        solver.parameters.max_time_in_seconds = self.max_time_seconds
        model.maximize(total_value_expr)
        primary_status = solver.solve(model)
        if primary_status == cp_model.INFEASIBLE:
            warnings.append("锁定物品无法全部放入可用容器")
            return self._error_result(
                SolveStatus.INFEASIBLE, current_value, warnings, started
            )
        if primary_status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            warnings.append("求解器未在时限内找到合法方案")
            return self._error_result(SolveStatus.ERROR, current_value, warnings, started)

        primary_optimal = primary_status == cp_model.OPTIMAL
        best_total = int(solver.value(total_value_expr))
        final_solver = solver
        final_status = primary_status

        if primary_optimal:
            model.add(total_value_expr == best_total)
            final_solver, final_status = self._solve_secondary(
                model, safe_value_expr, started, maximize=True
            )
            if final_status in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
                best_safe = int(final_solver.value(safe_value_expr))
                model.add(safe_value_expr == best_safe)
                final_solver, final_status = self._solve_secondary(
                    model, action_expr, started, maximize=False
                )
                if final_status in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
                    best_actions = int(final_solver.value(action_expr))
                    model.add(action_expr == best_actions)
                    final_solver, final_status = self._solve_secondary(
                        model, weight_expr, started, maximize=False
                    )

        if final_status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            # 次级目标耗尽时限不会推翻已证明的最高总价值，重新取主目标解。
            solver.parameters.max_time_in_seconds = 0.2
            model.maximize(total_value_expr)
            solver.solve(model)
            final_solver = solver

        selected_ids = {
            item_id
            for item_id, variable in selected.items()
            if final_solver.value(variable)
        }
        placements: list[Placement] = []
        for index, candidate in enumerate(candidates):
            if final_solver.value(candidate_vars[index]):
                placements.append(
                    Placement(
                        instance_id=candidate.item_id,
                        container_id=candidate.container_id,
                        x=candidate.x,
                        y=candidate.y,
                        width=candidate.width,
                        height=candidate.height,
                    )
                )
        protected_value = sum(
            values[placement.instance_id]
            for placement in placements
            if containers.get(placement.container_id)
            and containers[placement.container_id].kind == "safe_box"
        )
        target_value = sum(values[item_id] for item_id in selected_ids)
        actions = self.action_planner.build(
            state, placements, selected_ids, values
        )
        return PlanResult(
            status=SolveStatus.OPTIMAL if primary_optimal else SolveStatus.FEASIBLE,
            current_value=current_value,
            target_value=target_value,
            net_gain=target_value - current_value,
            protected_value=protected_value,
            placements=placements,
            selected_instance_ids=selected_ids,
            actions=actions,
            warnings=warnings,
            solve_seconds=time.perf_counter() - started,
        )

    def _solve_secondary(
        self,
        model: cp_model.CpModel,
        expression: cp_model.LinearExpr,
        started: float,
        *,
        maximize: bool,
    ) -> tuple[cp_model.CpSolver, cp_model.CpSolverStatus]:
        remaining = max(0.05, self.max_time_seconds - (time.perf_counter() - started))
        if maximize:
            model.maximize(expression)
        else:
            model.minimize(expression)
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = self.worker_count
        solver.parameters.max_time_in_seconds = remaining
        return solver, solver.solve(model)

    def _build_candidates(
        self,
        top_level_items: list[ItemInstance],
        all_items: list[ItemInstance],
        containers: dict[str, ContainerDefinition],
        item_by_id: dict[str, ItemInstance],
    ) -> list[PlacementCandidate]:
        candidates: list[PlacementCandidate] = []
        for item in all_items:
            definition = self.catalog.items[item.definition_id]
            for container in containers.values():
                if container.kind == "weapon_slot":
                    if definition.category != "weapon":
                        continue
                    slot_name = container.id.removeprefix("weapon_slot_")
                    if definition.weapon_slots and slot_name not in definition.weapon_slots:
                        continue
                    candidates.append(
                        PlacementCandidate(
                            item_id=item.instance_id,
                            container_id=container.id,
                            x=0,
                            y=0,
                            width=1,
                            height=1,
                            occupied_cells=((0, 0),),
                            action_cost=self._action_cost(item, container.id),
                        )
                    )
                    continue
                if container.kind == "equipment_slot":
                    slot_name = container.id.removeprefix("equipment_slot_")
                    if (
                        slot_name not in definition.equipment_slots
                        and definition.category != slot_name
                    ):
                        continue
                    candidates.append(
                        PlacementCandidate(
                            item_id=item.instance_id,
                            container_id=container.id,
                            x=0,
                            y=0,
                            width=1,
                            height=1,
                            occupied_cells=((0, 0),),
                            action_cost=self._action_cost(item, container.id),
                        )
                    )
                    continue
                if not self._container_allowed(definition, container):
                    continue
                required_container_item_id = container.source_item_instance_id
                for width, height in definition.orientations():
                    for y in range(container.height - height + 1):
                        for x in range(container.width - width + 1):
                            cells = tuple(
                                (cx, cy)
                                for cy in range(y, y + height)
                                for cx in range(x, x + width)
                            )
                            if any(cell in container.blocked_cells for cell in cells):
                                continue
                            candidates.append(
                                PlacementCandidate(
                                    item_id=item.instance_id,
                                    container_id=container.id,
                                    x=x,
                                    y=y,
                                    width=width,
                                    height=height,
                                    occupied_cells=cells,
                                    safe=container.kind == "safe_box",
                                    action_cost=self._action_cost(item, container.id),
                                    required_container_item_id=required_container_item_id,
                                )
                            )

            if (
                definition.category in {"attachment", "magazine"}
                and definition.attachment_type
            ):
                for weapon in top_level_items:
                    if weapon.instance_id == item.instance_id:
                        continue
                    weapon_definition = self.catalog.items[weapon.definition_id]
                    if weapon_definition.category != "weapon":
                        continue
                    if (
                        definition.compatible_weapons
                        and weapon.definition_id not in definition.compatible_weapons
                    ):
                        continue
                    capacity = weapon_definition.attachment_slots.get(
                        definition.attachment_type, 0
                    )
                    if capacity <= 0:
                        continue
                    container_id = (
                        f"weapon:{weapon.instance_id}:{definition.attachment_type}"
                    )
                    candidates.append(
                        PlacementCandidate(
                            item_id=item.instance_id,
                            container_id=container_id,
                            x=0,
                            y=0,
                            width=0,
                            height=0,
                            occupied_cells=(),
                            action_cost=1,
                            required_weapon_id=weapon.instance_id,
                            attachment_slot=definition.attachment_type,
                        )
                    )
            if definition.category == "ammo":
                for magazine in all_items:
                    magazine_definition = self.catalog.items[magazine.definition_id]
                    if (
                        magazine_definition.category != "magazine"
                        or magazine_definition.ammo_capacity <= 0
                        or (
                            definition.caliber
                            and magazine_definition.caliber
                            and definition.caliber != magazine_definition.caliber
                        )
                    ):
                        continue
                    candidates.append(
                        PlacementCandidate(
                            item_id=item.instance_id,
                            container_id=f"magazine:{magazine.instance_id}",
                            x=0,
                            y=0,
                            width=0,
                            height=0,
                            occupied_cells=(),
                            action_cost=1,
                            required_magazine_id=magazine.instance_id,
                        )
                    )
        return candidates

    @staticmethod
    def _descendant_ids(
        parent: ItemInstance,
        item_by_id: dict[str, ItemInstance],
    ) -> set[str]:
        descendants: set[str] = set()
        pending = list(parent.child_instance_ids)
        while pending:
            child_id = pending.pop()
            if child_id in descendants:
                continue
            descendants.add(child_id)
            child = item_by_id.get(child_id)
            if child:
                pending.extend(child.child_instance_ids)
        return descendants

    @staticmethod
    def _virtual_weapon_slots() -> dict[str, ContainerDefinition]:
        return {
            "weapon_slot_primary": ContainerDefinition(
                id="weapon_slot_primary",
                name="1号武器栏",
                kind="weapon_slot",
                width=1,
                height=1,
            ),
            "weapon_slot_secondary": ContainerDefinition(
                id="weapon_slot_secondary",
                name="2号武器栏",
                kind="weapon_slot",
                width=1,
                height=1,
            ),
        }

    @staticmethod
    def _virtual_equipment_slots() -> dict[str, ContainerDefinition]:
        return {
            "equipment_slot_backpack": ContainerDefinition(
                id="equipment_slot_backpack",
                name="背包装备栏",
                kind="equipment_slot",
                width=1,
                height=1,
            ),
            "equipment_slot_chest_rig": ContainerDefinition(
                id="equipment_slot_chest_rig",
                name="胸挂装备栏",
                kind="equipment_slot",
                width=1,
                height=1,
            ),
        }

    @staticmethod
    def _container_allowed(definition, container: ContainerDefinition) -> bool:
        if container.kind == "safe_box" and not definition.safe_box_allowed:
            return False
        return (
            container.id in definition.allowed_containers
            or container.kind in definition.allowed_containers
        )

    @staticmethod
    def _action_cost(item: ItemInstance, target_container_id: str) -> int:
        if item.origin == Origin.LOOT:
            return 1
        return 0 if item.source_container_id == target_container_id else 1

    @staticmethod
    def _error_result(
        status: SolveStatus,
        current_value: int,
        warnings: list[str],
        started: float,
    ) -> PlanResult:
        return PlanResult(
            status=status,
            current_value=current_value,
            target_value=current_value,
            net_gain=0,
            protected_value=0,
            placements=[],
            selected_instance_ids=set(),
            actions=[],
            warnings=warnings,
            solve_seconds=time.perf_counter() - started,
        )
