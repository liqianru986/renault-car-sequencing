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
from renault_cs.algorithms.greedy import construct_greedy_sequence

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
        paint_search_trials = int(params.get("paint_search_trials", 20))
        chain_search_trials = int(params.get("chain_search_trials", 0))
        block_search_trials = int(params.get("block_search_trials", 0))
        structured_search_interval = int(params.get("structured_search_interval", 50))
        vfls_trials = int(params.get("vfls_trials", 100))
        vfls_interval = int(params.get("vfls_interval", 10))

        greedy_ids = construct_greedy_sequence(instance, rng)
        seqrank_ids = tuple(
            vehicle.ident
            for vehicle in sorted(
                instance.planning_day_vehicles,
                key=lambda vehicle: (vehicle.original_rank, vehicle.ident),
            )
        )
        initial_candidates = (
                ("greedy", greedy_ids, self._evaluate(instance, greedy_ids, include_details=True)),
                (
                    "seqrank",
                    seqrank_ids,
                    self._evaluate(instance, seqrank_ids, include_details=True),
                ),
            )
        feasible_initials = [item for item in initial_candidates if item[2].is_feasible]
        initial_source, initial_ids, current_eval = min(
            feasible_initials,
            key=lambda item: item[2].objective_vector,
        )
        current_ids = initial_ids
        initial_score = current_eval.official_score
        best_ids, best_eval = current_ids, current_eval
        best_found_sec = 0.0
        convergence_trace: list[dict[str, object]] = [
            {
                "iteration": 0,
                "runtime_sec": 0.0,
                "score": best_eval.official_score,
                "objective_vector": best_eval.objective_vector,
                "source": f"{initial_source}_initial",
            }
        ]
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
        paint_search_improvements = 0
        chain_search_improvements = 0
        vfls_improvements = 0
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
            if self._is_better(local_eval, current_eval):
                current_ids, current_eval = local_ids, local_eval
                local_search_improvements += 1
                if self._is_better(local_eval, best_eval):
                    best_ids, best_eval = local_ids, local_eval
                    best_found_sec = perf_counter() - started_at
                    self._record_trace(
                        convergence_trace, iterations, best_found_sec, best_eval, "ratio_swap"
                    )

            if iterations % vfls_interval == 0:
                local_ids, local_eval, accepted_vfls = self._incremental_vfls_search(
                    instance,
                    current_ids,
                    current_eval,
                    rng,
                    vfls_trials,
                    deadline,
                )
                if accepted_vfls:
                    current_ids, current_eval = local_ids, local_eval
                    vfls_improvements += accepted_vfls
                    if self._is_better(local_eval, best_eval):
                        best_ids, best_eval = local_ids, local_eval
                        best_found_sec = perf_counter() - started_at
                        self._record_trace(
                            convergence_trace, iterations, best_found_sec, best_eval, "vfls"
                        )

            local_ids, local_eval = self._paint_relocate_search(
                instance,
                current_ids,
                current_eval,
                rng,
                paint_search_trials,
                deadline,
            )
            if self._is_better(local_eval, current_eval):
                current_ids, current_eval = local_ids, local_eval
                paint_search_improvements += 1
                if self._is_better(local_eval, best_eval):
                    best_ids, best_eval = local_ids, local_eval
                    best_found_sec = perf_counter() - started_at
                    self._record_trace(
                        convergence_trace, iterations, best_found_sec, best_eval, "paint_relocate"
                    )

            if (
                (chain_search_trials > 0 or block_search_trials > 0)
                and current_eval.hprc_violations == 0
                and iterations > 0
                and iterations % structured_search_interval == 0
            ):
                local_ids, local_eval = self._hprc_preserving_search(
                    instance,
                    current_ids,
                    current_eval,
                    rng,
                    chain_search_trials,
                    block_search_trials,
                    deadline,
                )
                if self._is_better(local_eval, current_eval):
                    current_ids, current_eval = local_ids, local_eval
                    chain_search_improvements += 1
                    if self._is_better(local_eval, best_eval):
                        best_ids, best_eval = local_ids, local_eval
                        best_found_sec = perf_counter() - started_at
                        self._record_trace(
                            convergence_trace,
                            iterations,
                            best_found_sec,
                            best_eval,
                            "hprc_preserving",
                        )

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

            is_best = candidate_eval.is_feasible and self._is_better(candidate_eval, best_eval)
            is_improvement = candidate_eval.is_feasible and self._is_better(candidate_eval, current_eval)
            accepted = is_improvement or (
                candidate_eval.is_feasible
                and rng.random() < math.exp(-max(0, candidate_score - current_score) / temperature)
            )

            reward = 0.0
            if is_best:
                best_ids, best_eval = candidate_ids, candidate_eval
                best_found_sec = perf_counter() - started_at
                self._record_trace(
                    convergence_trace, iterations, best_found_sec, best_eval, "alns"
                )
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
                "initial_source": initial_source,
                "best_score": best_eval.official_score,
                "destroy_fraction": destroy_fraction,
                "max_destroy_count": max_destroy_count,
                "regret_sample_size": regret_sample_size,
                "local_search_trials": local_search_trials,
                "local_search_improvements": local_search_improvements,
                "paint_search_trials": paint_search_trials,
                "paint_search_improvements": paint_search_improvements,
                "chain_search_trials": chain_search_trials,
                "block_search_trials": block_search_trials,
                "structured_search_interval": structured_search_interval,
                "chain_search_improvements": chain_search_improvements,
                "vfls_trials": vfls_trials,
                "vfls_interval": vfls_interval,
                "vfls_improvements": vfls_improvements,
                "convergence_trace": convergence_trace,
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
    def _is_better(candidate: EvaluationResult, incumbent: EvaluationResult) -> bool:
        """按赛题给定的目标先后顺序比较，而不是让低优先级抵消高优先级。"""

        return candidate.objective_vector < incumbent.objective_vector

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
    def _record_trace(
        trace: list[dict[str, object]],
        iteration: int,
        runtime_sec: float,
        evaluation: EvaluationResult,
        source: str,
    ) -> None:
        trace.append(
            {
                "iteration": iteration,
                "runtime_sec": round(runtime_sec, 4),
                "score": evaluation.official_score,
                "objective_vector": evaluation.objective_vector,
                "source": source,
            }
        )

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
        positions = self._insertion_candidates(
            instance, sequence, vehicle_id, candidate_limit, rng
        )

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

    @staticmethod
    def _insertion_candidates(
        instance: ProblemInstance,
        sequence: list[str],
        vehicle_id: str,
        candidate_limit: int,
        rng: random.Random,
    ) -> list[int]:
        """优先检查原顺序附近和同色批次边界，再用随机位置补足候选集。"""

        size = len(sequence)
        vehicle = instance.vehicle_by_id[vehicle_id]
        preferred = {0, size, min(size, max(0, vehicle.original_rank - 1))}
        for position, ident in enumerate(sequence):
            if instance.vehicle_by_id[ident].paint_color == vehicle.paint_color:
                preferred.update((position, position + 1))

        preferred_positions = sorted(preferred)
        if len(preferred_positions) > candidate_limit:
            anchors = {0, size, min(size, max(0, vehicle.original_rank - 1))}
            others = [position for position in preferred_positions if position not in anchors]
            remaining = max(0, candidate_limit - len(anchors))
            preferred_positions = sorted(anchors | set(rng.sample(others, remaining)))

        if len(preferred_positions) < candidate_limit:
            remaining = [
                position
                for position in range(size + 1)
                if position not in preferred
            ]
            sample_size = min(candidate_limit - len(preferred_positions), len(remaining))
            preferred_positions.extend(rng.sample(remaining, sample_size))
        return sorted(preferred_positions)

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
            if candidate_eval.is_feasible and self._is_better(candidate_eval, best_eval):
                best_ids, best_eval = candidate_ids, candidate_eval

        return best_ids, best_eval

    def _incremental_vfls_search(
        self,
        instance: ProblemInstance,
        sequence: tuple[str, ...],
        evaluation: EvaluationResult,
        rng: random.Random,
        trials: int,
        deadline: float,
    ) -> tuple[tuple[str, ...], EvaluationResult, int]:
        """用增量 Swap/Insert/Reflection 执行 first-improvement 局部搜索。"""

        if trials <= 0 or len(sequence) < 2:
            return sequence, evaluation, 0
        state = IncrementalEvaluationState(instance, sequence)
        current_vector = evaluation.objective_vector
        accepted = 0
        denominators = [item.denominator for item in instance.ratio_constraints]

        for _ in range(trials):
            if perf_counter() >= deadline:
                break
            move = rng.choices(("swap", "insert", "reflect"), weights=(7, 2, 1), k=1)[0]
            first = rng.randrange(len(sequence))
            if denominators and rng.random() < 0.35:
                distance = rng.choice(denominators)
                second = min(
                    len(sequence) - 1,
                    max(0, first + rng.choice((-distance, distance))),
                )
            else:
                second = rng.randrange(len(sequence))
            if first == second:
                continue

            if move == "swap":
                state.swap(first, second)
                undo = lambda: state.swap(first, second)
            elif move == "insert":
                state.insert(first, second)
                undo = lambda: state.insert(second, first)
            else:
                state.reflect(first, second)
                undo = lambda: state.reflect(first, second)

            score = state.score()
            if score.paint_feasible and score.objective_vector < current_vector:
                current_vector = score.objective_vector
                accepted += 1
            else:
                undo()

        if not accepted:
            return sequence, evaluation, 0
        improved_ids = state.vehicle_ids
        improved_eval = self._evaluate(instance, improved_ids, include_details=True)
        return improved_ids, improved_eval, accepted

    def _paint_relocate_search(
        self,
        instance: ProblemInstance,
        sequence: tuple[str, ...],
        evaluation: EvaluationResult,
        rng: random.Random,
        trials: int,
        deadline: float,
    ) -> tuple[tuple[str, ...], EvaluationResult]:
        """优先做不改变装配负荷的同类型换色，再尝试颜色批次合并。"""

        if trials <= 0 or len(sequence) < 3:
            return sequence, evaluation

        boundary_positions = [
            position
            for position, ident in enumerate(sequence)
            if (position == 0 or instance.vehicle_by_id[sequence[position - 1]].paint_color
                != instance.vehicle_by_id[ident].paint_color)
            or (position == len(sequence) - 1
                or instance.vehicle_by_id[sequence[position + 1]].paint_color
                != instance.vehicle_by_id[ident].paint_color)
        ]
        best_ids, best_eval = sequence, evaluation

        for _ in range(trials):
            if perf_counter() >= deadline or not boundary_positions:
                break
            source = rng.choice(boundary_positions)
            color = instance.vehicle_by_id[sequence[source]].paint_color

            # option_flags 相同的车辆交换位置时，各滑动窗口负荷完全不变。
            flags = instance.vehicle_by_id[sequence[source]].option_flags
            equivalent_targets = [
                position
                for position, ident in enumerate(sequence)
                if position != source
                and instance.vehicle_by_id[ident].option_flags == flags
                and instance.vehicle_by_id[ident].paint_color != color
            ]
            if equivalent_targets:
                sampled_targets = (
                    equivalent_targets
                    if len(equivalent_targets) <= 20
                    else rng.sample(equivalent_targets, 20)
                )
                best_swap: tuple[str, ...] | None = None
                best_paint_changes = evaluation.paint_changes
                for target in sampled_targets:
                    candidate = list(sequence)
                    candidate[source], candidate[target] = candidate[target], candidate[source]
                    paint_changes = self._paint_change_count(instance, candidate)
                    if paint_changes < best_paint_changes:
                        best_paint_changes = paint_changes
                        best_swap = tuple(candidate)
                if best_swap is not None:
                    candidate_eval = self._evaluate(instance, best_swap, include_details=True)
                    if candidate_eval.is_feasible and self._is_better(candidate_eval, best_eval):
                        best_ids, best_eval = best_swap, candidate_eval
                        continue

            targets = [
                position
                for position, ident in enumerate(sequence)
                if position != source
                and instance.vehicle_by_id[ident].paint_color == color
            ]
            if not targets:
                continue

            anchor = rng.choice(targets)
            candidate = list(sequence)
            vehicle_id = candidate.pop(source)
            if source < anchor:
                anchor -= 1
            insert_at = anchor if rng.random() < 0.5 else anchor + 1
            candidate.insert(insert_at, vehicle_id)
            candidate_ids = tuple(candidate)
            candidate_eval = self._evaluate(instance, candidate_ids, include_details=True)
            if candidate_eval.is_feasible and self._is_better(candidate_eval, best_eval):
                best_ids, best_eval = candidate_ids, candidate_eval

        return best_ids, best_eval

    @staticmethod
    def _paint_change_count(instance: ProblemInstance, sequence: list[str]) -> int:
        """快速计算颜色切换次数，用于局部搜索候选预筛。"""

        colors = [instance.vehicle_by_id[ident].paint_color for ident in sequence]
        previous = instance.previous_day_vehicles
        changes = int(bool(previous) and bool(colors) and previous[-1].paint_color != colors[0])
        changes += sum(left != right for left, right in zip(colors, colors[1:]))
        return changes

    def _hprc_preserving_search(
        self,
        instance: ProblemInstance,
        sequence: tuple[str, ...],
        evaluation: EvaluationResult,
        rng: random.Random,
        chain_trials: int,
        block_trials: int,
        deadline: float,
    ) -> tuple[tuple[str, ...], EvaluationResult]:
        """在每个位置的 HPRC 签名不变时尝试三点循环和等长块交换。"""

        if chain_trials <= 0 and block_trials <= 0:
            return sequence, evaluation

        high_indices = tuple(
            index
            for index, constraint in enumerate(instance.ratio_constraints)
            if constraint.is_high_priority
        )
        if not high_indices:
            return sequence, evaluation

        def signature(vehicle_id: str) -> tuple[bool, ...]:
            flags = instance.vehicle_by_id[vehicle_id].option_flags
            return tuple(flags[index] for index in high_indices)

        boundaries = [
            position
            for position, ident in enumerate(sequence)
            if position == 0
            or instance.vehicle_by_id[sequence[position - 1]].paint_color
            != instance.vehicle_by_id[ident].paint_color
            or position == len(sequence) - 1
            or instance.vehicle_by_id[sequence[position + 1]].paint_color
            != instance.vehicle_by_id[ident].paint_color
        ]
        positions_by_signature: dict[tuple[bool, ...], list[int]] = {}
        for position, ident in enumerate(sequence):
            positions_by_signature.setdefault(signature(ident), []).append(position)

        best_ids, best_eval = sequence, evaluation
        for _ in range(chain_trials):
            if perf_counter() >= deadline or not boundaries:
                break
            first = rng.choice(boundaries)
            compatible = positions_by_signature[signature(sequence[first])]
            if len(compatible) < 3:
                continue
            other = rng.sample([position for position in compatible if position != first], 2)
            positions = (first, other[0], other[1])
            for direction in (1, -1):
                candidate = list(sequence)
                values = [sequence[position] for position in positions]
                rotated = values[direction:] + values[:direction]
                for position, vehicle_id in zip(positions, rotated):
                    candidate[position] = vehicle_id
                if self._paint_change_count(instance, candidate) > evaluation.paint_changes:
                    continue
                candidate_ids = tuple(candidate)
                candidate_eval = self._evaluate(instance, candidate_ids, include_details=True)
                if candidate_eval.is_feasible and self._is_better(candidate_eval, best_eval):
                    best_ids, best_eval = candidate_ids, candidate_eval

        max_block_length = 3
        for _ in range(block_trials):
            if perf_counter() >= deadline or len(sequence) < 2:
                break
            length = rng.randint(2, min(max_block_length, len(sequence) // 2))
            first = rng.randrange(0, len(sequence) - length + 1)
            first_signatures = tuple(signature(ident) for ident in sequence[first:first + length])
            candidates = [
                start
                for start in range(0, len(sequence) - length + 1)
                if abs(start - first) >= length
                and tuple(signature(ident) for ident in sequence[start:start + length])
                == first_signatures
            ]
            if not candidates:
                continue
            second = rng.choice(candidates)
            candidate = list(sequence)
            left = sequence[first:first + length]
            right = sequence[second:second + length]
            candidate[first:first + length] = right
            candidate[second:second + length] = left
            if self._paint_change_count(instance, candidate) > evaluation.paint_changes:
                continue
            candidate_ids = tuple(candidate)
            candidate_eval = self._evaluate(instance, candidate_ids, include_details=True)
            if candidate_eval.is_feasible and self._is_better(candidate_eval, best_eval):
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
