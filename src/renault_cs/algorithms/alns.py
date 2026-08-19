"""ALNS：破坏、修复、模拟退火接受与算子权重自适应。"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Callable

from renault_cs.application.config import SolveConfig
from renault_cs.domain.enums import ObjectiveKind
from renault_cs.domain.models import ProblemInstance
from renault_cs.domain.solution import SequenceSolution, SolveResult
from renault_cs.evaluation.paint import evaluate_paint
from renault_cs.evaluation.incremental import IncrementalEvaluationState
from renault_cs.evaluation.ratio import evaluate_ratios
from renault_cs.evaluation.scoring import build_objective_vector, calculate_weighted_score

if TYPE_CHECKING:
    from renault_cs.application.ports import SolutionEvaluator
    from renault_cs.domain.evaluation import EvaluationResult


@dataclass(slots=True)
class _Operator:
    """一个可自适应选择的算子及其运行统计。"""

    name: str
    function: Callable
    weight: float = 1.0
    reward: float = 0.0
    segment_uses: int = 0
    total_uses: int = 0
    accepted: int = 0
    improved: int = 0
    best: int = 0


class AlnsSolver:
    """面向 Renault Car Sequencing 的最小完整 ALNS 求解器。"""

    def __init__(self, evaluator: SolutionEvaluator) -> None:
        self._evaluator = evaluator

    @property
    def name(self) -> str:
        return "alns"

    def solve(self, instance: ProblemInstance, config: SolveConfig) -> SolveResult:
        params = config.algorithm_parameters
        rng = random.Random(config.seed)
        started_at = perf_counter()
        deadline = started_at + config.time_limit_sec

        destroy_fraction = float(params.get("destroy_fraction", 0.12))
        reaction = float(params.get("reaction", 0.20))
        segment_length = int(params.get("segment_length", 50))
        cooling = float(params.get("cooling", 0.995))
        candidate_limit = int(params.get("candidate_limit", 30))
        max_destroy_count = int(params.get("max_destroy_count", 8))
        regret_sample_size = int(params.get("regret_sample_size", 6))
        local_search_trials = int(params.get("local_search_trials", 20))

        initial_ids = tuple(
            vehicle.ident
            for vehicle in sorted(
                instance.planning_day_vehicles,
                key=lambda vehicle: (vehicle.original_rank, vehicle.ident),
            )
        )
        current_ids = initial_ids
        current_eval = self._evaluate(instance, current_ids, include_details=True)
        initial_score = current_eval.official_score
        best_ids, best_eval = current_ids, current_eval
        best_found_sec = 0.0
        temperature = max(1.0, float(current_eval.official_score or 1) * 0.02)

        destroys = [
            _Operator("random", self._destroy_random),
            _Operator("violation_focused", self._destroy_violation_focused),
            _Operator("same_color", self._destroy_same_color),
        ]
        repairs = [
            _Operator("greedy", self._repair_greedy),
            _Operator("regret_2", self._repair_regret_2),
        ]

        iterations = accepted_count = improvement_count = 0
        local_search_improvements = 0
        while perf_counter() < deadline and (
            config.max_iterations is None or iterations < config.max_iterations
        ):
            local_ids, local_eval = self._violation_swap_search(
                instance,
                current_ids,
                current_eval,
                rng,
                local_search_trials,
                deadline,
            )
            if self._score(local_eval) < self._score(current_eval):
                current_ids, current_eval = local_ids, local_eval
                local_search_improvements += 1
                if self._score(local_eval) < self._score(best_eval):
                    best_ids, best_eval = local_ids, local_eval
                    best_found_sec = perf_counter() - started_at

            if perf_counter() >= deadline:
                break

            destroy = self._choose_operator(destroys, rng)
            repair = self._choose_operator(repairs, rng)
            remove_count = max(
                2,
                min(
                    len(current_ids) - 1,
                    max_destroy_count,
                    round(len(current_ids) * destroy_fraction),
                ),
            )

            partial, removed = destroy.function(
                instance, current_ids, current_eval, remove_count, rng
            )
            candidate_ids = repair.function(
                instance,
                partial,
                removed,
                rng,
                candidate_limit,
                regret_sample_size,
                deadline,
            )
            if len(candidate_ids) != len(initial_ids):
                break

            candidate_eval = self._evaluate(instance, candidate_ids, include_details=True)
            candidate_score = self._score(candidate_eval)
            current_score = self._score(current_eval)
            best_score = self._score(best_eval)

            is_best = candidate_eval.is_feasible and candidate_score < best_score
            is_improvement = candidate_eval.is_feasible and candidate_score < current_score
            accepted = is_improvement or (
                candidate_eval.is_feasible
                and rng.random() < math.exp(-max(0, candidate_score - current_score) / temperature)
            )

            reward = 0.0
            if is_best:
                best_ids, best_eval = candidate_ids, candidate_eval
                best_found_sec = perf_counter() - started_at
                reward = 8.0
                destroy.best += 1
                repair.best += 1
            elif is_improvement:
                reward = 4.0
                destroy.improved += 1
                repair.improved += 1
            elif accepted:
                reward = 1.0

            if accepted:
                current_ids, current_eval = candidate_ids, candidate_eval
                accepted_count += 1
                destroy.accepted += 1
                repair.accepted += 1
            if is_improvement:
                improvement_count += 1

            for operator in (destroy, repair):
                operator.reward += reward
                operator.segment_uses += 1
                operator.total_uses += 1

            iterations += 1
            temperature = max(1e-6, temperature * cooling)
            if iterations % segment_length == 0:
                self._update_weights(destroys, reaction)
                self._update_weights(repairs, reaction)

        runtime_sec = perf_counter() - started_at
        solution = SequenceSolution(
            instance_name=instance.name,
            vehicle_ids=best_ids,
            algorithm=self.name,
            runtime_sec=runtime_sec,
            seed=config.seed,
            metadata={
                "iterations": iterations,
                "accepted": accepted_count,
                "improvements": improvement_count,
                "best_found_sec": best_found_sec,
                "initial_score": initial_score,
                "best_score": best_eval.official_score,
                "destroy_fraction": destroy_fraction,
                "max_destroy_count": max_destroy_count,
                "regret_sample_size": regret_sample_size,
                "local_search_trials": local_search_trials,
                "local_search_improvements": local_search_improvements,
                "destroy_operators": self._operator_stats(destroys),
                "repair_operators": self._operator_stats(repairs),
            },
        )
        return SolveResult(
            solution=solution,
            evaluation=self._evaluator.evaluate(instance, solution),
            status="completed",
        )

    def _evaluate(
        self,
        instance: ProblemInstance,
        vehicle_ids: tuple[str, ...],
        *,
        include_details: bool = False,
    ) -> EvaluationResult:
        solution = SequenceSolution(instance.name, vehicle_ids, self.name)
        return self._evaluator.evaluate(instance, solution, include_details=include_details)

    @staticmethod
    def _score(evaluation: EvaluationResult) -> int:
        return int(evaluation.official_score or 10**18)

    @staticmethod
    def _choose_operator(operators: list[_Operator], rng: random.Random) -> _Operator:
        return rng.choices(operators, weights=[item.weight for item in operators], k=1)[0]

    @staticmethod
    def _update_weights(operators: list[_Operator], reaction: float) -> None:
        for operator in operators:
            if operator.segment_uses:
                performance = operator.reward / operator.segment_uses
                operator.weight = (1.0 - reaction) * operator.weight + reaction * performance
            operator.weight = max(0.05, operator.weight)
            operator.reward = 0.0
            operator.segment_uses = 0

    @staticmethod
    def _operator_stats(operators: list[_Operator]) -> dict[str, object]:
        return {
            item.name: {
                "weight": round(item.weight, 4),
                "uses": item.total_uses,
                "accepted": item.accepted,
                "improved": item.improved,
                "best": item.best,
            }
            for item in operators
        }

    @staticmethod
    def _remove_positions(
        sequence: tuple[str, ...], positions: set[int]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        partial = tuple(ident for index, ident in enumerate(sequence) if index not in positions)
        removed = tuple(ident for index, ident in enumerate(sequence) if index in positions)
        return partial, removed

    def _destroy_random(
        self,
        instance: ProblemInstance,
        sequence: tuple[str, ...],
        evaluation: EvaluationResult,
        remove_count: int,
        rng: random.Random,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        positions = set(rng.sample(range(len(sequence)), remove_count))
        return self._remove_positions(sequence, positions)

    def _destroy_violation_focused(
        self,
        instance: ProblemInstance,
        sequence: tuple[str, ...],
        evaluation: EvaluationResult,
        remove_count: int,
        rng: random.Random,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        positions: set[int] = set()
        details = sorted(
            evaluation.ratio_violation_details,
            key=lambda item: item.violation_count,
            reverse=True,
        )
        for detail in details:
            constraint_index = instance.constraint_index[detail.constraint_id]
            candidates = [
                position
                for position in range(max(0, detail.window_start), min(len(sequence), detail.window_end + 1))
                if instance.vehicle_by_id[sequence[position]].option_flags[constraint_index]
                and position not in positions
            ]
            rng.shuffle(candidates)
            positions.update(candidates[: remove_count - len(positions)])
            if len(positions) >= remove_count:
                break
        remaining = [position for position in range(len(sequence)) if position not in positions]
        if len(positions) < remove_count:
            positions.update(rng.sample(remaining, remove_count - len(positions)))
        return self._remove_positions(sequence, positions)

    def _destroy_same_color(
        self,
        instance: ProblemInstance,
        sequence: tuple[str, ...],
        evaluation: EvaluationResult,
        remove_count: int,
        rng: random.Random,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        color = instance.vehicle_by_id[rng.choice(sequence)].paint_color
        same_color = [
            index
            for index, ident in enumerate(sequence)
            if instance.vehicle_by_id[ident].paint_color == color
        ]
        rng.shuffle(same_color)
        positions = set(same_color[:remove_count])
        remaining = [position for position in range(len(sequence)) if position not in positions]
        if len(positions) < remove_count:
            positions.update(rng.sample(remaining, remove_count - len(positions)))
        return self._remove_positions(sequence, positions)

    def _repair_greedy(
        self,
        instance: ProblemInstance,
        partial: tuple[str, ...],
        removed: tuple[str, ...],
        rng: random.Random,
        candidate_limit: int,
        regret_sample_size: int,
        deadline: float,
    ) -> tuple[str, ...]:
        sequence = list(partial)
        pending = list(removed)
        rng.shuffle(pending)
        for vehicle_id in pending:
            if perf_counter() >= deadline:
                sequence.insert(rng.randrange(len(sequence) + 1), vehicle_id)
                continue
            position, _ = self._best_insert(
                instance, sequence, vehicle_id, candidate_limit, rng, deadline
            )
            sequence.insert(position, vehicle_id)
        return tuple(sequence)

    def _repair_regret_2(
        self,
        instance: ProblemInstance,
        partial: tuple[str, ...],
        removed: tuple[str, ...],
        rng: random.Random,
        candidate_limit: int,
        regret_sample_size: int,
        deadline: float,
    ) -> tuple[str, ...]:
        sequence = list(partial)
        pending = list(removed)
        while pending:
            if perf_counter() >= deadline:
                rng.shuffle(pending)
                for vehicle_id in pending:
                    sequence.insert(rng.randrange(len(sequence) + 1), vehicle_id)
                break
            choices: list[tuple[int, int, str]] = []
            sampled_pending = (
                pending
                if len(pending) <= regret_sample_size
                else rng.sample(pending, regret_sample_size)
            )
            for vehicle_id in sampled_pending:
                best_position, costs = self._best_insert(
                    instance, sequence, vehicle_id, candidate_limit, rng, deadline
                )
                regret = costs[1] - costs[0] if len(costs) > 1 else 0
                choices.append((regret, best_position, vehicle_id))
                if perf_counter() >= deadline:
                    break
            _, position, vehicle_id = max(choices, key=lambda item: item[0])
            sequence.insert(position, vehicle_id)
            pending.remove(vehicle_id)
        return tuple(sequence)

    def _best_insert(
        self,
        instance: ProblemInstance,
        sequence: list[str],
        vehicle_id: str,
        candidate_limit: int,
        rng: random.Random,
        deadline: float,
    ) -> tuple[int, list[int]]:
        positions = list(range(len(sequence) + 1))
        if len(positions) > candidate_limit:
            positions = sorted(rng.sample(positions, candidate_limit))

        state = IncrementalEvaluationState(instance, [vehicle_id, *sequence])
        scored: list[tuple[int, int]] = []
        fallback: list[tuple[int, int]] = []
        current_position = 0
        for position in positions:
            while current_position < position:
                state.swap(current_position, current_position + 1)
                current_position += 1
            result = state.score()
            fallback.append((result.score, position))
            if result.paint_feasible:
                scored.append((result.score, position))
            if perf_counter() >= deadline:
                break
        ranked = sorted(scored or fallback)
        return ranked[0][1], [score for score, _ in ranked[:2]]

    def _violation_swap_search(
        self,
        instance: ProblemInstance,
        sequence: tuple[str, ...],
        evaluation: EvaluationResult,
        rng: random.Random,
        trials: int,
        deadline: float,
    ) -> tuple[tuple[str, ...], EvaluationResult]:
        """用少量定向交换强化当前解，只接受完整可行的字典序改善。"""

        if trials <= 0 or not evaluation.ratio_violation_details:
            return sequence, evaluation

        details = sorted(
            evaluation.ratio_violation_details,
            key=lambda item: item.violation_count,
            reverse=True,
        )
        best_ids, best_eval = sequence, evaluation

        for _ in range(trials):
            if perf_counter() >= deadline:
                break
            detail = rng.choice(details[: min(12, len(details))])
            constraint_index = instance.constraint_index[detail.constraint_id]
            window_positions = [
                position
                for position in range(max(0, detail.window_start), min(len(sequence), detail.window_end + 1))
                if instance.vehicle_by_id[sequence[position]].option_flags[constraint_index]
            ]
            if not window_positions:
                continue
            source_color = instance.vehicle_by_id[sequence[rng.choice(window_positions)]].paint_color
            same_color_outside = [
                position
                for position, ident in enumerate(sequence)
                if not (detail.window_start <= position <= detail.window_end)
                and not instance.vehicle_by_id[ident].option_flags[constraint_index]
                and instance.vehicle_by_id[ident].paint_color == source_color
            ]
            outside_positions = same_color_outside or [
                position
                for position, ident in enumerate(sequence)
                if not (detail.window_start <= position <= detail.window_end)
                and not instance.vehicle_by_id[ident].option_flags[constraint_index]
            ]
            if not outside_positions:
                continue

            same_color_sources = [
                position
                for position in window_positions
                if instance.vehicle_by_id[sequence[position]].paint_color == source_color
            ]
            left = rng.choice(same_color_sources)
            right = rng.choice(outside_positions)
            candidate = list(sequence)
            candidate[left], candidate[right] = candidate[right], candidate[left]
            candidate_ids = tuple(candidate)
            candidate_eval = self._evaluate(instance, candidate_ids, include_details=True)
            if candidate_eval.is_feasible and self._score(candidate_eval) < self._score(best_eval):
                best_ids, best_eval = candidate_ids, candidate_eval

        return best_ids, best_eval

    @staticmethod
    def _partial_score(instance: ProblemInstance, vehicle_ids: list[str]) -> tuple[int, bool]:
        sequence = tuple(instance.vehicle_by_id[ident] for ident in vehicle_ids)
        paint = evaluate_paint(
            instance.previous_day_vehicles,
            sequence,
            instance.paint_batch_limit,
        )
        ratios = evaluate_ratios(
            instance.previous_day_vehicles,
            sequence,
            instance.ratio_constraints,
        )
        vector = build_objective_vector(
            instance.objectives,
            {
                ObjectiveKind.PAINT_COLOR_CHANGES: paint.changes,
                ObjectiveKind.HPRC_VIOLATIONS: ratios.hprc_violations,
                ObjectiveKind.LPRC_VIOLATIONS: ratios.lprc_violations,
            },
        )
        return calculate_weighted_score(vector), paint.is_batch_feasible
