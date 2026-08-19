"""ALNS邻域增量评估：局部交换时仅更新受影响的滑动窗口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from renault_cs.domain.enums import ObjectiveKind
from renault_cs.domain.models import ProblemInstance, Vehicle
from renault_cs.evaluation.paint import evaluate_paint
from renault_cs.evaluation.scoring import build_objective_vector, calculate_weighted_score


@dataclass(frozen=True, slots=True)
class IncrementalScore:
    """一个邻域候选的轻量评分结果。"""

    score: int
    objective_vector: tuple[int, ...]
    paint_feasible: bool


class IncrementalEvaluationState:
    """维护一条可变序列的比例约束负荷，支持精确相邻交换增量更新。"""

    def __init__(self, instance: ProblemInstance, vehicle_ids: Sequence[str]) -> None:
        self._instance = instance
        self._vehicle_ids = list(vehicle_ids)
        self._vehicles = [instance.vehicle_by_id[ident] for ident in vehicle_ids]
        self._first_starts: list[int] = []
        self._window_loads: list[list[int]] = []
        self._constraint_violations: list[int] = []
        self._build_ratio_state()

    @property
    def vehicle_ids(self) -> tuple[str, ...]:
        return tuple(self._vehicle_ids)

    def swap(self, left: int, right: int) -> None:
        """交换两个D日位置，并增量更新所有受影响的N/P窗口。"""

        if left == right:
            return
        if not (0 <= left < len(self._vehicles) and 0 <= right < len(self._vehicles)):
            raise IndexError("swap position is outside the planning sequence")

        left_vehicle = self._vehicles[left]
        right_vehicle = self._vehicles[right]
        for constraint_index, constraint in enumerate(self._instance.ratio_constraints):
            left_flag = int(left_vehicle.option_flags[constraint_index])
            right_flag = int(right_vehicle.option_flags[constraint_index])
            if left_flag == right_flag:
                continue

            first_start = self._first_starts[constraint_index]
            loads = self._window_loads[constraint_index]
            affected_starts = self._covering_starts(left, constraint.denominator)
            affected_starts.update(self._covering_starts(right, constraint.denominator))

            for start in affected_starts:
                load_index = start - first_start
                old_load = loads[load_index]
                old_violation = max(0, old_load - constraint.numerator)
                delta = 0
                if start <= left < start + constraint.denominator:
                    delta += right_flag - left_flag
                if start <= right < start + constraint.denominator:
                    delta += left_flag - right_flag
                if delta == 0:
                    continue
                new_load = old_load + delta
                loads[load_index] = new_load
                self._constraint_violations[constraint_index] += (
                    max(0, new_load - constraint.numerator) - old_violation
                )

        self._vehicles[left], self._vehicles[right] = self._vehicles[right], self._vehicles[left]
        self._vehicle_ids[left], self._vehicle_ids[right] = (
            self._vehicle_ids[right],
            self._vehicle_ids[left],
        )

    def score(self) -> IncrementalScore:
        """按完整Evaluator的目标定义返回当前状态的精确轻量评分。"""

        hprc = 0
        lprc = 0
        for constraint, violation in zip(
            self._instance.ratio_constraints,
            self._constraint_violations,
            strict=True,
        ):
            if constraint.is_high_priority:
                hprc += violation
            else:
                lprc += violation

        paint = evaluate_paint(
            self._instance.previous_day_vehicles,
            self._vehicles,
            self._instance.paint_batch_limit,
        )
        vector = build_objective_vector(
            self._instance.objectives,
            {
                ObjectiveKind.PAINT_COLOR_CHANGES: paint.changes,
                ObjectiveKind.HPRC_VIOLATIONS: hprc,
                ObjectiveKind.LPRC_VIOLATIONS: lprc,
            },
        )
        return IncrementalScore(
            score=calculate_weighted_score(vector),
            objective_vector=vector,
            paint_feasible=paint.is_batch_feasible,
        )

    def _build_ratio_state(self) -> None:
        previous = self._instance.previous_day_vehicles
        previous_count = len(previous)
        combined = tuple(previous) + tuple(self._vehicles)
        planning_count = len(self._vehicles)

        for constraint_index, constraint in enumerate(self._instance.ratio_constraints):
            first_start = max(-previous_count, -(constraint.denominator - 1))
            flags = [int(vehicle.option_flags[constraint_index]) for vehicle in combined]
            loads: list[int] = []
            violation_total = 0
            for start in range(first_start, planning_count):
                combined_start = previous_count + start
                combined_end = min(
                    combined_start + constraint.denominator,
                    len(combined),
                )
                load = sum(flags[combined_start:combined_end])
                loads.append(load)
                violation_total += max(0, load - constraint.numerator)
            self._first_starts.append(first_start)
            self._window_loads.append(loads)
            self._constraint_violations.append(violation_total)

    def _covering_starts(self, position: int, denominator: int) -> set[int]:
        first_start = max(
            -len(self._instance.previous_day_vehicles),
            -(denominator - 1),
        )
        last_start = len(self._vehicles) - 1
        return set(
            range(
                max(first_start, position - denominator + 1),
                min(position, last_start) + 1,
            )
        )
