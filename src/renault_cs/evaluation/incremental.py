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
    """维护可变序列的窗口负荷和涂装状态，支持三类精确增量移动。"""

    def __init__(self, instance: ProblemInstance, vehicle_ids: Sequence[str]) -> None:
        self._instance = instance
        self._vehicle_ids = list(vehicle_ids)
        self._vehicles = [instance.vehicle_by_id[ident] for ident in vehicle_ids]
        self._first_starts: list[int] = []
        self._window_loads: list[list[int]] = []
        self._constraint_violations: list[int] = []
        self._build_ratio_state()
        paint = evaluate_paint(
            instance.previous_day_vehicles,
            self._vehicles,
            instance.paint_batch_limit,
        )
        self._paint_changes = paint.changes
        self._long_batch_count = sum(
            batch.length > instance.paint_batch_limit for batch in paint.batches
        )

    @property
    def vehicle_ids(self) -> tuple[str, ...]:
        return tuple(self._vehicle_ids)

    def swap(self, left: int, right: int) -> None:
        """交换两个D日位置，并增量更新所有受影响的N/P窗口。"""

        if left == right:
            return
        if not (0 <= left < len(self._vehicles) and 0 <= right < len(self._vehicles)):
            raise IndexError("swap position is outside the planning sequence")

        affected_edges = {left, left + 1, right, right + 1}
        old_edges = self._edge_changes(affected_edges)
        old_long_batches = self._affected_long_batches({left, right})

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
        self._paint_changes += self._edge_changes(affected_edges) - old_edges
        self._long_batch_count += (
            self._affected_long_batches({left, right}) - old_long_batches
        )

    def insert(self, source: int, target: int) -> None:
        """把 source 车辆移动到 target 最终位置，支持前向和后向插入。"""

        self._validate_position(source)
        self._validate_position(target)
        if source == target:
            return
        start, end = sorted((source, target))
        segment = self._vehicle_ids[start:end + 1]
        if source < target:
            reordered = [*segment[1:], segment[0]]
        else:
            reordered = [segment[-1], *segment[:-1]]
        self._replace_segment(start, reordered)

    def reflect(self, start: int, end: int) -> None:
        """反转闭区间 [start, end]，即 VFLS 的 Reflection 移动。"""

        self._validate_position(start)
        self._validate_position(end)
        if start > end:
            start, end = end, start
        if start == end:
            return
        self._replace_segment(start, list(reversed(self._vehicle_ids[start:end + 1])))

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

        vector = build_objective_vector(
            self._instance.objectives,
            {
                ObjectiveKind.PAINT_COLOR_CHANGES: self._paint_changes,
                ObjectiveKind.HPRC_VIOLATIONS: hprc,
                ObjectiveKind.LPRC_VIOLATIONS: lprc,
            },
        )
        return IncrementalScore(
            score=calculate_weighted_score(vector),
            objective_vector=vector,
            paint_feasible=self._long_batch_count == 0,
        )

    def _replace_segment(self, start: int, vehicle_ids: list[str]) -> None:
        """替换等长连续区间，并只更新与该区间相交的窗口和颜色批次。"""

        end = start + len(vehicle_ids) - 1
        old_vehicles = self._vehicles[start:end + 1]
        new_vehicles = [self._instance.vehicle_by_id[ident] for ident in vehicle_ids]
        affected_edges = set(range(start, end + 2))
        old_edges = self._edge_changes(affected_edges)
        old_long_batches = self._long_batches_intersecting(start, end)

        for constraint_index, constraint in enumerate(self._instance.ratio_constraints):
            deltas = [
                int(new.option_flags[constraint_index]) - int(old.option_flags[constraint_index])
                for old, new in zip(old_vehicles, new_vehicles, strict=True)
            ]
            if not any(deltas):
                continue
            prefix = [0]
            for delta in deltas:
                prefix.append(prefix[-1] + delta)
            first_start = self._first_starts[constraint_index]
            loads = self._window_loads[constraint_index]
            affected_starts = range(
                max(first_start, start - constraint.denominator + 1),
                min(end, len(self._vehicles) - 1) + 1,
            )
            for window_start in affected_starts:
                overlap_start = max(start, window_start)
                overlap_end = min(end, window_start + constraint.denominator - 1)
                delta = prefix[overlap_end - start + 1] - prefix[overlap_start - start]
                if delta == 0:
                    continue
                load_index = window_start - first_start
                old_load = loads[load_index]
                new_load = old_load + delta
                loads[load_index] = new_load
                self._constraint_violations[constraint_index] += (
                    max(0, new_load - constraint.numerator)
                    - max(0, old_load - constraint.numerator)
                )

        self._vehicle_ids[start:end + 1] = vehicle_ids
        self._vehicles[start:end + 1] = new_vehicles
        self._paint_changes += self._edge_changes(affected_edges) - old_edges
        self._long_batch_count += (
            self._long_batches_intersecting(start, end) - old_long_batches
        )

    def _edge_changes(self, edges: set[int]) -> int:
        changes = 0
        previous = self._instance.previous_day_vehicles
        for right in edges:
            if right == 0 and self._vehicles and previous:
                changes += previous[-1].paint_color != self._vehicles[0].paint_color
            elif 0 < right < len(self._vehicles):
                changes += (
                    self._vehicles[right - 1].paint_color
                    != self._vehicles[right].paint_color
                )
        return changes

    def _affected_long_batches(self, positions: set[int]) -> int:
        runs: set[tuple[int, int]] = set()
        for position in positions:
            for neighbor in (position - 1, position, position + 1):
                if 0 <= neighbor < len(self._vehicles):
                    runs.add(self._run_bounds(neighbor))
        return sum(end - start + 1 > self._instance.paint_batch_limit for start, end in runs)

    def _long_batches_intersecting(self, start: int, end: int) -> int:
        left = self._run_bounds(max(0, start - 1))[0]
        right = self._run_bounds(min(len(self._vehicles) - 1, end + 1))[1]
        count = 0
        position = left
        while position <= right:
            run_start, run_end = self._run_bounds(position)
            count += run_end - run_start + 1 > self._instance.paint_batch_limit
            position = run_end + 1
        return count

    def _run_bounds(self, position: int) -> tuple[int, int]:
        color = self._vehicles[position].paint_color
        start = position
        end = position
        while start > 0 and self._vehicles[start - 1].paint_color == color:
            start -= 1
        while end + 1 < len(self._vehicles) and self._vehicles[end + 1].paint_color == color:
            end += 1
        return start, end

    def _validate_position(self, position: int) -> None:
        if not 0 <= position < len(self._vehicles):
            raise IndexError("move position is outside the planning sequence")

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
