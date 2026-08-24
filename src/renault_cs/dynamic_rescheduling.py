"""动态滚动重排：紧急插单、冻结窗口与分时段 N/P 工位降产。"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

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
    official_score_weight: int
    moved_vehicle_weight: int
    position_shift_weight: int
    max_moved_vehicles: int
    max_total_position_shift: int


@dataclass(frozen=True, slots=True)
class DynamicAssessment:
    official_score: int
    hprc_violations: int
    lprc_violations: int
    paint_changes: int
    paint_feasible: bool
    temporary_capacity_violations: int
    moved_vehicle_count: int
    total_position_shift: int
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
        official_score_weight=int(payload.get("official_score_weight", 100)),
        moved_vehicle_weight=int(payload.get("moved_vehicle_weight", 50_000)),
        position_shift_weight=int(payload.get("position_shift_weight", 2_000)),
        max_moved_vehicles=int(payload.get("max_moved_vehicles", 10**9)),
        max_total_position_shift=int(
            payload.get("max_total_position_shift", 10**12)
        ),
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
        baseline_ids: tuple[str, ...] | None = None,
    ) -> ReschedulingResult:
        extended = extend_instance(instance, scenario)
        original_ids = baseline_ids or tuple(
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
            extended,
            current,
            original_ids,
            scenario.capacity_changes,
            scenario.official_score_weight,
            scenario.moved_vehicle_weight,
            scenario.position_shift_weight,
        )
        best, best_assessment = current, current_assessment

        operators = ["random", "capacity_focused", "same_color"]
        weights = {name: 1.0 for name in operators}
        rewards = {name: 0.0 for name in operators}
        uses = {name: 0 for name in operators}
        temperature = max(1.0, current_assessment.official_score * 0.01)
        iterations = accepted = 0
        local_search_improvements = 0

        while perf_counter() < deadline:
            local, local_assessment = DynamicGurobiRescheduler._capacity_swap_search(
                extended,
                current,
                current_assessment,
                fixed_length,
                original_ids,
                scenario,
                rng,
                deadline,
            )
            if local_assessment.search_score < current_assessment.search_score:
                current, current_assessment = local, local_assessment
                local_search_improvements += 1
                if local_assessment.search_score < best_assessment.search_score:
                    best, best_assessment = local, local_assessment

            operator = rng.choices(operators, weights=[weights[name] for name in operators], k=1)[0]
            mutable_count = len(current) - fixed_length
            remove_count = max(2, min(mutable_count, 8))
            partial, removed = DynamicGurobiRescheduler._destroy(
                extended,
                current,
                fixed_length,
                remove_count,
                operator,
                scenario.capacity_changes,
                rng,
            )
            candidate = DynamicGurobiRescheduler._repair(
                extended,
                partial,
                removed,
                fixed_length,
                original_ids,
                scenario.capacity_changes,
                scenario.official_score_weight,
                scenario.moved_vehicle_weight,
                scenario.position_shift_weight,
                rng,
                deadline,
            )
            candidate_assessment = assess_dynamic_solution(
                extended,
                candidate,
                original_ids,
                scenario.capacity_changes,
                scenario.official_score_weight,
                scenario.moved_vehicle_weight,
                scenario.position_shift_weight,
            )
            delta = candidate_assessment.search_score - current_assessment.search_score
            plan_stable = (
                candidate_assessment.moved_vehicle_count <= scenario.max_moved_vehicles
                and candidate_assessment.total_position_shift
                <= scenario.max_total_position_shift
            )
            accept = candidate_assessment.paint_feasible and plan_stable and (
                delta < 0 or rng.random() < math.exp(-max(0, delta) / temperature)
            )

            reward = 0.0
            if candidate_assessment.paint_feasible and plan_stable and (
                candidate_assessment.search_score < best_assessment.search_score
            ):
                best, best_assessment = candidate, candidate_assessment
                reward = 8.0
            elif candidate_assessment.paint_feasible and plan_stable and delta < 0:
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
                "local_search_improvements": local_search_improvements,
                "operator_weights": {name: round(value, 4) for name, value in weights.items()},
            },
        )
        return ReschedulingResult(
            solution=solution,
            before=assess_dynamic_solution(
                extended,
                baseline_ids,
                original_ids,
                scenario.capacity_changes,
                scenario.official_score_weight,
                scenario.moved_vehicle_weight,
                scenario.position_shift_weight,
            ),
            after=best_assessment,
            fixed_prefix_length=fixed_length,
            iterations=iterations,
            accepted_iterations=accepted,
            operator_weights={name: round(value, 4) for name, value in weights.items()},
        )


class DynamicGurobiRescheduler:
    """车辆级动态MILP：冻结计划、临时能力和位置偏移在同一模型中决策。"""

    def solve(
        self,
        instance: ProblemInstance,
        scenario: DynamicScenario,
        baseline_ids: tuple[str, ...] | None = None,
    ) -> ReschedulingResult:
        import gurobipy as gp
        from gurobipy import GRB

        extended = extend_instance(instance, scenario)
        original_ids = baseline_ids or tuple(
            vehicle.ident
            for vehicle in sorted(
                instance.planning_day_vehicles,
                key=lambda vehicle: (vehicle.original_rank, vehicle.ident),
            )
        )
        emergency_ids = tuple(vehicle.ident for vehicle in scenario.emergency_orders)
        baseline = original_ids + emergency_ids
        vehicle_ids = baseline
        count = len(vehicle_ids)
        positions = range(count)
        vehicle_index = range(count)
        id_to_index = {ident: index for index, ident in enumerate(vehicle_ids)}
        fixed_length = min(
            len(original_ids), scenario.current_position + scenario.freeze_length
        )

        model = gp.Model(f"dynamic_{scenario.name}")
        model.Params.TimeLimit = scenario.time_limit_sec
        model.Params.Seed = scenario.seed
        model.Params.OutputFlag = 0
        x = model.addVars(vehicle_index, positions, vtype=GRB.BINARY, name="x")
        colors = sorted({extended.vehicle_by_id[ident].paint_color for ident in vehicle_ids})
        paint = model.addVars(colors, positions, vtype=GRB.BINARY, name="paint")
        changes = model.addVars(positions, vtype=GRB.BINARY, name="change")

        model.addConstrs(
            (gp.quicksum(x[v, p] for v in vehicle_index) == 1 for p in positions),
            name="one_vehicle_per_position",
        )
        model.addConstrs(
            (gp.quicksum(x[v, p] for p in positions) == 1 for v in vehicle_index),
            name="one_position_per_vehicle",
        )
        for position in range(fixed_length):
            model.addConstr(
                x[id_to_index[original_ids[position]], position] == 1,
                name=f"frozen_{position}",
            )

        for color in colors:
            matching = [
                v
                for v, ident in enumerate(vehicle_ids)
                if extended.vehicle_by_id[ident].paint_color == color
            ]
            model.addConstrs(
                (paint[color, p] == gp.quicksum(x[v, p] for v in matching) for p in positions),
                name=f"paint_link_{color}",
            )
        previous_color = (
            extended.previous_day_vehicles[-1].paint_color
            if extended.previous_day_vehicles
            else None
        )
        if previous_color in colors:
            model.addConstr(changes[0] >= 1 - paint[previous_color, 0])
        else:
            model.addConstr(changes[0] == int(previous_color is not None))
        for position in range(1, count):
            for color in colors:
                model.addConstr(
                    changes[position] >= paint[color, position - 1] - paint[color, position]
                )
        batch_window = extended.paint_batch_limit + 1
        for color in colors:
            for start in range(count - batch_window + 1):
                model.addConstr(
                    gp.quicksum(paint[color, p] for p in range(start, start + batch_window))
                    <= extended.paint_batch_limit
                )

        hprc_terms: list[Any] = []
        lprc_terms: list[Any] = []
        previous = extended.previous_day_vehicles
        for constraint_index, constraint in enumerate(extended.ratio_constraints):
            matching = [
                v
                for v, ident in enumerate(vehicle_ids)
                if extended.vehicle_by_id[ident].option_flags[constraint_index]
            ]
            first_start = max(-len(previous), -(constraint.denominator - 1))
            for start in range(first_start, count):
                planning_positions = range(max(0, start), min(count, start + constraint.denominator))
                previous_start = len(previous) + start
                previous_end = min(len(previous), previous_start + constraint.denominator)
                previous_load = sum(
                    vehicle.option_flags[constraint_index]
                    for vehicle in previous[max(0, previous_start):previous_end]
                )
                violation = model.addVar(lb=0.0, name=f"ratio_{constraint_index}_{start}")
                model.addConstr(
                    violation
                    >= previous_load
                    + gp.quicksum(x[v, p] for v in matching for p in planning_positions)
                    - constraint.numerator
                )
                (hprc_terms if constraint.is_high_priority else lprc_terms).append(violation)

        temporary_terms: list[Any] = []
        for change_index, change in enumerate(scenario.capacity_changes):
            constraint_index = extended.constraint_index[change.constraint_id]
            matching = [
                v
                for v, ident in enumerate(vehicle_ids)
                if extended.vehicle_by_id[ident].option_flags[constraint_index]
            ]
            for start in range(max(0, change.start_position), min(change.end_position, count - 1) + 1):
                violation = model.addVar(lb=0.0, name=f"temporary_{change_index}_{start}")
                model.addConstr(
                    violation
                    >= gp.quicksum(
                        x[v, p]
                        for v in matching
                        for p in range(start, min(count, start + change.denominator))
                    )
                    - change.numerator
                )
                temporary_terms.append(violation)

        moved = model.addVars(range(len(original_ids)), vtype=GRB.BINARY, name="moved")
        shift = model.addVars(range(len(original_ids)), lb=0.0, name="shift")
        for original_position, ident in enumerate(original_ids):
            v = id_to_index[ident]
            actual_position = gp.quicksum(p * x[v, p] for p in positions)
            model.addConstr(shift[original_position] >= actual_position - original_position)
            model.addConstr(shift[original_position] >= original_position - actual_position)
            model.addConstr(moved[original_position] >= 1 - x[v, original_position])
        model.addConstr(
            gp.quicksum(moved.values()) <= scenario.max_moved_vehicles,
            name="moved_budget",
        )
        model.addConstr(
            gp.quicksum(shift.values()) <= scenario.max_total_position_shift,
            name="shift_budget",
        )

        objective_values = {
            ObjectiveKind.HPRC_VIOLATIONS: gp.quicksum(hprc_terms),
            ObjectiveKind.LPRC_VIOLATIONS: gp.quicksum(lprc_terms),
            ObjectiveKind.PAINT_COLOR_CHANGES: gp.quicksum(changes.values()),
        }
        official_weighted = gp.quicksum(
            objective_values[objective.kind] * 100 ** (2 - rank)
            for rank, objective in enumerate(extended.objectives)
        )
        disruption = (
            scenario.moved_vehicle_weight * gp.quicksum(moved.values())
            + scenario.position_shift_weight * gp.quicksum(shift.values())
        )
        model.setObjectiveN(gp.quicksum(temporary_terms), 0, priority=2, name="temporary_capacity")
        model.setObjectiveN(
            scenario.official_score_weight * official_weighted + disruption,
            1,
            priority=1,
            name="quality_and_stability",
        )
        for position, ident in enumerate(baseline):
            x[id_to_index[ident], position].Start = 1.0

        started_at = perf_counter()
        model.optimize()
        runtime = perf_counter() - started_at
        if model.SolCount == 0:
            model.dispose()
            raise RuntimeError("Dynamic Gurobi finished without a feasible solution")
        sequence = tuple(
            vehicle_ids[max(vehicle_index, key=lambda v: x[v, position].X)]
            for position in positions
        )
        assessment = assess_dynamic_solution(
            extended,
            sequence,
            original_ids,
            scenario.capacity_changes,
            scenario.official_score_weight,
            scenario.moved_vehicle_weight,
            scenario.position_shift_weight,
        )
        solution = SequenceSolution(
            instance_name=extended.name,
            vehicle_ids=sequence,
            algorithm="dynamic_gurobi",
            runtime_sec=runtime,
            seed=scenario.seed,
            metadata={
                "status": int(model.Status),
                "solution_count": int(model.SolCount),
                "mip_gap": None,
                "best_bound": None,
                "gap_note": "Gurobi多目标模型不提供单一MIPGap/ObjBound。",
                "fixed_prefix_length": fixed_length,
            },
        )
        before = assess_dynamic_solution(
            extended,
            baseline,
            original_ids,
            scenario.capacity_changes,
            scenario.official_score_weight,
            scenario.moved_vehicle_weight,
            scenario.position_shift_weight,
        )
        model.dispose()
        return ReschedulingResult(
            solution=solution,
            before=before,
            after=assessment,
            fixed_prefix_length=fixed_length,
            iterations=0,
            accepted_iterations=0,
            operator_weights={},
        )

    @staticmethod
    def _capacity_swap_search(
        instance: ProblemInstance,
        sequence: tuple[str, ...],
        assessment: DynamicAssessment,
        fixed_length: int,
        original_ids: tuple[str, ...],
        scenario: DynamicScenario,
        rng: random.Random,
        deadline: float,
    ) -> tuple[tuple[str, ...], DynamicAssessment]:
        """同色交换修复临时产能窗口，减少对既有颜色序列和计划的扰动。"""

        windows = _temporary_violation_windows(
            instance,
            sequence,
            scenario.capacity_changes,
        )
        if not windows:
            return sequence, assessment

        best, best_assessment = sequence, assessment
        for _ in range(20):
            if perf_counter() >= deadline:
                break
            change, start = rng.choice(windows)
            constraint_index = instance.constraint_index[change.constraint_id]
            end = min(start + change.denominator, len(sequence))
            source_positions = [
                position
                for position in range(max(fixed_length, start), end)
                if instance.vehicle_by_id[sequence[position]].option_flags[constraint_index]
            ]
            if not source_positions:
                continue
            left = rng.choice(source_positions)
            source_color = instance.vehicle_by_id[sequence[left]].paint_color
            same_color_targets = [
                position
                for position in range(fixed_length, len(sequence))
                if not (start <= position < end)
                and not instance.vehicle_by_id[sequence[position]].option_flags[constraint_index]
                and instance.vehicle_by_id[sequence[position]].paint_color == source_color
            ]
            if not same_color_targets:
                continue
            right = rng.choice(same_color_targets)
            candidate = list(sequence)
            candidate[left], candidate[right] = candidate[right], candidate[left]
            candidate_ids = tuple(candidate)
            candidate_assessment = assess_dynamic_solution(
                instance,
                candidate_ids,
                original_ids,
                scenario.capacity_changes,
                scenario.official_score_weight,
                scenario.moved_vehicle_weight,
                scenario.position_shift_weight,
            )
            if (
                candidate_assessment.paint_feasible
                and candidate_assessment.moved_vehicle_count
                <= scenario.max_moved_vehicles
                and candidate_assessment.total_position_shift
                <= scenario.max_total_position_shift
                and candidate_assessment.search_score < best_assessment.search_score
            ):
                best, best_assessment = candidate_ids, candidate_assessment

        return best, best_assessment

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
        official_score_weight: int,
        moved_vehicle_weight: int,
        position_shift_weight: int,
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
            if len(positions) > 30:
                positions = sorted(rng.sample(positions, 30))
            best_position = positions[0]
            best_score = math.inf
            for position in positions:
                candidate = sequence.copy()
                candidate.insert(position, vehicle_id)
                assessment = assess_partial_sequence(
                    instance,
                    candidate,
                    original_ids,
                    changes,
                    official_score_weight,
                    moved_vehicle_weight,
                    position_shift_weight,
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
    official_score_weight: int = 100,
    moved_vehicle_weight: int = 50_000,
    position_shift_weight: int = 1,
) -> DynamicAssessment:
    """评估完整动态序列，官方指标与扩展指标分开保留。"""

    solution = SequenceSolution(instance.name, vehicle_ids, "dynamic_assessment")
    official = RenaultEvaluator().evaluate(instance, solution)
    return _make_assessment(
        instance,
        vehicle_ids,
        original_ids,
        changes,
        official,
        official_score_weight,
        moved_vehicle_weight,
        position_shift_weight,
    )


def assess_partial_sequence(
    instance: ProblemInstance,
    vehicle_ids: list[str],
    original_ids: tuple[str, ...],
    changes: tuple[CapacityChange, ...],
    official_score_weight: int = 100,
    moved_vehicle_weight: int = 50_000,
    position_shift_weight: int = 1,
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
    return _make_assessment(
        instance,
        tuple(vehicle_ids),
        original_ids,
        changes,
        evaluation,
        official_score_weight,
        moved_vehicle_weight,
        position_shift_weight,
    )


def _make_assessment(
    instance: ProblemInstance,
    vehicle_ids: tuple[str, ...],
    original_ids: tuple[str, ...],
    changes: tuple[CapacityChange, ...],
    official: object,
    official_score_weight: int,
    moved_vehicle_weight: int,
    position_shift_weight: int,
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
    total_shift = sum(shifts)
    official_score = int(official.official_score)
    disruption_score = (
        moved_count * moved_vehicle_weight
        + total_shift * position_shift_weight
    )
    # 临时产能作为最高优先级；静态质量与计划扰动按场景权重共同权衡。
    search_score = (
        dynamic_violations * 1_000_000_000_000_000_000
        + official_score * official_score_weight
        + disruption_score
    )
    return DynamicAssessment(
        official_score=official_score,
        hprc_violations=official.hprc_violations,
        lprc_violations=official.lprc_violations,
        paint_changes=official.paint_changes,
        paint_feasible=official.paint_batch_feasible,
        temporary_capacity_violations=dynamic_violations,
        moved_vehicle_count=moved_count,
        total_position_shift=total_shift,
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


def _temporary_violation_windows(
    instance: ProblemInstance,
    vehicle_ids: tuple[str, ...],
    changes: tuple[CapacityChange, ...],
) -> list[tuple[CapacityChange, int]]:
    """返回当前序列中超过临时N/P能力的窗口。"""

    windows: list[tuple[CapacityChange, int]] = []
    sequence = tuple(instance.vehicle_by_id[ident] for ident in vehicle_ids)
    for change in changes:
        constraint_index = instance.constraint_index[change.constraint_id]
        final_start = min(change.end_position, len(sequence) - 1)
        for start in range(max(0, change.start_position), final_start + 1):
            window = sequence[start : start + change.denominator]
            observed = sum(vehicle.option_flags[constraint_index] for vehicle in window)
            if observed > change.numerator:
                windows.append((change, start))
    return windows
