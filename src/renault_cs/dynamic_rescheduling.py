"""动态滚动重排：紧急插单、冻结窗口与分时段 N/P 工位降产。"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from renault_cs.domain.models import ProblemInstance, Vehicle
from renault_cs.domain.solution import SequenceSolution
from renault_cs.evaluation.evaluator import RenaultEvaluator
from renault_cs.evaluation.paint import evaluate_paint
from renault_cs.evaluation.ratio import evaluate_ratios
from renault_cs.evaluation.scoring import build_objective_vector, calculate_weighted_score
from renault_cs.domain.enums import ObjectiveKind


@dataclass(frozen=True, slots=True)
class CapacityChange:
    constraint_id: str
    start_position: int
    end_position: int
    numerator: int
    denominator: int


@dataclass(frozen=True, slots=True)
class DynamicScenario:
    name: str
    instance_path: Path
    current_position: int
    freeze_length: int
    emergency_orders: tuple[Vehicle, ...]
    capacity_changes: tuple[CapacityChange, ...]
    time_limit_sec: float
    seed: int


@dataclass(frozen=True, slots=True)
class DynamicAssessment:
    official_score: int
    hprc_violations: int
    lprc_violations: int
    paint_changes: int
    paint_feasible: bool
    temporary_capacity_violations: int
    moved_vehicle_count: int
    average_position_shift: float
    maximum_position_shift: int
    search_score: int


@dataclass(frozen=True, slots=True)
class ReschedulingResult:
    solution: SequenceSolution
    before: DynamicAssessment
    after: DynamicAssessment
    fixed_prefix_length: int
    iterations: int
    accepted_iterations: int
    operator_weights: dict[str, float]


def load_dynamic_scenario(path: Path) -> DynamicScenario:
    """从 JSON 存档读取可复现的动态事件案例。"""

    path = Path(path).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    emergency_orders = tuple(
        Vehicle(
            ident=item["ident"],
            production_date=item["production_date"],
            original_rank=int(item["original_rank"]),
            paint_color=str(item["paint_color"]),
            option_flags=tuple(bool(value) for value in item["option_flags"]),
        )
        for item in payload["emergency_orders"]
    )
    changes = tuple(
        CapacityChange(
            constraint_id=item["constraint_id"],
            start_position=int(item["start_position"]),
            end_position=int(item["end_position"]),
            numerator=int(item["numerator"]),
            denominator=int(item["denominator"]),
        )
        for item in payload["capacity_changes"]
    )
    return DynamicScenario(
        name=payload["name"],
        instance_path=(path.parent / payload["instance_path"]).resolve(),
        current_position=int(payload["current_position"]),
        freeze_length=int(payload["freeze_length"]),
        emergency_orders=emergency_orders,
        capacity_changes=changes,
        time_limit_sec=float(payload.get("time_limit_sec", 10.0)),
        seed=int(payload.get("seed", 42)),
    )


def extend_instance(instance: ProblemInstance, scenario: DynamicScenario) -> ProblemInstance:
    """把紧急订单加入 D 日车辆集合，原始 instance 保持不变。"""

    return ProblemInstance(
        name=f"{instance.name}__{scenario.name}",
        paint_batch_limit=instance.paint_batch_limit,
        ratio_constraints=instance.ratio_constraints,
        objectives=instance.objectives,
        previous_day_vehicles=instance.previous_day_vehicles,
        planning_day_vehicles=instance.planning_day_vehicles + scenario.emergency_orders,
    )


class RollingAlnsRescheduler:
    """固定已执行/冻结前缀，仅对剩余车辆执行轻量 ALNS。"""

    def __init__(self, evaluator: RenaultEvaluator) -> None:
        self._evaluator = evaluator

    def solve(
        self,
        instance: ProblemInstance,
        scenario: DynamicScenario,
    ) -> ReschedulingResult:
        extended = extend_instance(instance, scenario)
        original_ids = tuple(
            vehicle.ident
            for vehicle in sorted(
                instance.planning_day_vehicles,
                key=lambda vehicle: (vehicle.original_rank, vehicle.ident),
            )
        )
        fixed_length = min(
            len(original_ids),
            scenario.current_position + scenario.freeze_length,
        )
        fixed_prefix = original_ids[:fixed_length]
        baseline_ids = fixed_prefix + original_ids[fixed_length:] + tuple(
            vehicle.ident for vehicle in scenario.emergency_orders
        )

        rng = random.Random(scenario.seed)
        started_at = perf_counter()
        deadline = started_at + scenario.time_limit_sec
        current = baseline_ids
        current_assessment = assess_dynamic_solution(
            extended, current, original_ids, scenario.capacity_changes
        )
        best, best_assessment = current, current_assessment

        operators = ["random", "capacity_focused", "same_color"]
        weights = {name: 1.0 for name in operators}
        rewards = {name: 0.0 for name in operators}
        uses = {name: 0 for name in operators}
        temperature = max(1.0, current_assessment.search_score * 0.01)
        iterations = accepted = 0

        while perf_counter() < deadline:
            operator = rng.choices(operators, weights=[weights[name] for name in operators], k=1)[0]
            mutable_count = len(current) - fixed_length
            remove_count = max(2, min(mutable_count, round(mutable_count * 0.15)))
            partial, removed = self._destroy(
                extended,
                current,
                fixed_length,
                remove_count,
                operator,
                scenario.capacity_changes,
                rng,
            )
            candidate = self._repair(
                extended,
                partial,
                removed,
                fixed_length,
                original_ids,
                scenario.capacity_changes,
                rng,
                deadline,
            )
            candidate_assessment = assess_dynamic_solution(
                extended, candidate, original_ids, scenario.capacity_changes
            )
            delta = candidate_assessment.search_score - current_assessment.search_score
            accept = candidate_assessment.paint_feasible and (
                delta < 0 or rng.random() < math.exp(-max(0, delta) / temperature)
            )

            reward = 0.0
            if candidate_assessment.paint_feasible and (
                candidate_assessment.search_score < best_assessment.search_score
            ):
                best, best_assessment = candidate, candidate_assessment
                reward = 8.0
            elif delta < 0:
                reward = 4.0
            elif accept:
                reward = 1.0

            if accept:
                current, current_assessment = candidate, candidate_assessment
                accepted += 1
            rewards[operator] += reward
            uses[operator] += 1
            iterations += 1
            temperature = max(1e-6, temperature * 0.995)

            if iterations % 20 == 0:
                for name in operators:
                    if uses[name]:
                        weights[name] = max(
                            0.05,
                            0.8 * weights[name] + 0.2 * rewards[name] / uses[name],
                        )
                    rewards[name] = 0.0
                    uses[name] = 0

        solution = SequenceSolution(
            instance_name=extended.name,
            vehicle_ids=best,
            algorithm="rolling_alns",
            runtime_sec=perf_counter() - started_at,
            seed=scenario.seed,
            metadata={
                "scenario": scenario.name,
                "fixed_prefix_length": fixed_length,
                "iterations": iterations,
                "accepted_iterations": accepted,
                "operator_weights": {name: round(value, 4) for name, value in weights.items()},
            },
        )
        return ReschedulingResult(
            solution=solution,
            before=assess_dynamic_solution(
                extended, baseline_ids, original_ids, scenario.capacity_changes
            ),
            after=best_assessment,
            fixed_prefix_length=fixed_length,
            iterations=iterations,
            accepted_iterations=accepted,
            operator_weights={name: round(value, 4) for name, value in weights.items()},
        )

    @staticmethod
    def _destroy(
        instance: ProblemInstance,
        sequence: tuple[str, ...],
        fixed_length: int,
        remove_count: int,
        operator: str,
        changes: tuple[CapacityChange, ...],
        rng: random.Random,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        mutable_positions = list(range(fixed_length, len(sequence)))
        if operator == "same_color":
            color = instance.vehicle_by_id[sequence[rng.choice(mutable_positions)]].paint_color
            preferred = [
                position
                for position in mutable_positions
                if instance.vehicle_by_id[sequence[position]].paint_color == color
            ]
        elif operator == "capacity_focused" and changes:
            change = rng.choice(changes)
            constraint_index = instance.constraint_index[change.constraint_id]
            preferred = [
                position
                for position in mutable_positions
                if change.start_position <= position <= change.end_position
                and instance.vehicle_by_id[sequence[position]].option_flags[constraint_index]
            ]
        else:
            preferred = []

        rng.shuffle(preferred)
        selected = set(preferred[:remove_count])
        remaining = [position for position in mutable_positions if position not in selected]
        if len(selected) < remove_count:
            selected.update(rng.sample(remaining, remove_count - len(selected)))
        partial = tuple(ident for index, ident in enumerate(sequence) if index not in selected)
        removed = tuple(ident for index, ident in enumerate(sequence) if index in selected)
        return partial, removed

    @staticmethod
    def _repair(
        instance: ProblemInstance,
        partial: tuple[str, ...],
        removed: tuple[str, ...],
        fixed_length: int,
        original_ids: tuple[str, ...],
        changes: tuple[CapacityChange, ...],
        rng: random.Random,
        deadline: float,
    ) -> tuple[str, ...]:
        sequence = list(partial)
        pending = list(removed)
        rng.shuffle(pending)
        for vehicle_id in pending:
            if perf_counter() >= deadline:
                sequence.insert(rng.randrange(fixed_length, len(sequence) + 1), vehicle_id)
                continue
            positions = list(range(fixed_length, len(sequence) + 1))
            if len(positions) > 60:
                positions = sorted(rng.sample(positions, 60))
            best_position = positions[0]
            best_score = math.inf
            for position in positions:
                candidate = sequence.copy()
                candidate.insert(position, vehicle_id)
                assessment = assess_partial_sequence(
                    instance, candidate, original_ids, changes
                )
                if assessment.paint_feasible and assessment.search_score < best_score:
                    best_position = position
                    best_score = assessment.search_score
                if perf_counter() >= deadline:
                    break
            sequence.insert(best_position, vehicle_id)
        return tuple(sequence)


def assess_dynamic_solution(
    instance: ProblemInstance,
    vehicle_ids: tuple[str, ...],
    original_ids: tuple[str, ...],
    changes: tuple[CapacityChange, ...],
) -> DynamicAssessment:
    """评估完整动态序列，官方指标与扩展指标分开保留。"""

    solution = SequenceSolution(instance.name, vehicle_ids, "dynamic_assessment")
    official = RenaultEvaluator().evaluate(instance, solution)
    return _make_assessment(instance, vehicle_ids, original_ids, changes, official)


def assess_partial_sequence(
    instance: ProblemInstance,
    vehicle_ids: list[str],
    original_ids: tuple[str, ...],
    changes: tuple[CapacityChange, ...],
) -> DynamicAssessment:
    """Repair过程中评估不完整序列，不执行车辆集合完整性检查。"""

    sequence = tuple(instance.vehicle_by_id[ident] for ident in vehicle_ids)
    paint = evaluate_paint(instance.previous_day_vehicles, sequence, instance.paint_batch_limit)
    ratios = evaluate_ratios(instance.previous_day_vehicles, sequence, instance.ratio_constraints)
    vector = build_objective_vector(
        instance.objectives,
        {
            ObjectiveKind.PAINT_COLOR_CHANGES: paint.changes,
            ObjectiveKind.HPRC_VIOLATIONS: ratios.hprc_violations,
            ObjectiveKind.LPRC_VIOLATIONS: ratios.lprc_violations,
        },
    )

    @dataclass(frozen=True)
    class _Evaluation:
        official_score: int
        hprc_violations: int
        lprc_violations: int
        paint_changes: int
        paint_batch_feasible: bool

    evaluation = _Evaluation(
        calculate_weighted_score(vector),
        ratios.hprc_violations,
        ratios.lprc_violations,
        paint.changes,
        paint.is_batch_feasible,
    )
    return _make_assessment(instance, tuple(vehicle_ids), original_ids, changes, evaluation)


def _make_assessment(
    instance: ProblemInstance,
    vehicle_ids: tuple[str, ...],
    original_ids: tuple[str, ...],
    changes: tuple[CapacityChange, ...],
    official: object,
) -> DynamicAssessment:
    dynamic_violations = _temporary_capacity_violations(instance, vehicle_ids, changes)
    original_position = {ident: position for position, ident in enumerate(original_ids)}
    new_position = {ident: position for position, ident in enumerate(vehicle_ids)}
    shifts = [
        abs(new_position[ident] - position)
        for ident, position in original_position.items()
        if ident in new_position
    ]
    moved_count = sum(shift > 0 for shift in shifts)
    official_score = int(official.official_score)
    # 临时产能要求优先于原赛题目标；计划扰动只作同分方案的稳定性偏好。
    search_score = (
        dynamic_violations * 1_000_000_000_000
        + official_score * 10_000
        + moved_count
    )
    return DynamicAssessment(
        official_score=official_score,
        hprc_violations=official.hprc_violations,
        lprc_violations=official.lprc_violations,
        paint_changes=official.paint_changes,
        paint_feasible=official.paint_batch_feasible,
        temporary_capacity_violations=dynamic_violations,
        moved_vehicle_count=moved_count,
        average_position_shift=sum(shifts) / len(shifts) if shifts else 0.0,
        maximum_position_shift=max(shifts, default=0),
        search_score=search_score,
    )


def _temporary_capacity_violations(
    instance: ProblemInstance,
    vehicle_ids: tuple[str, ...],
    changes: tuple[CapacityChange, ...],
) -> int:
    total = 0
    sequence = tuple(instance.vehicle_by_id[ident] for ident in vehicle_ids)
    for change in changes:
        constraint_index = instance.constraint_index[change.constraint_id]
        final_start = min(change.end_position, len(sequence) - 1)
        for start in range(max(0, change.start_position), final_start + 1):
            window = sequence[start : start + change.denominator]
            observed = sum(vehicle.option_flags[constraint_index] for vehicle in window)
            total += max(0, observed - change.numerator)
    return total
