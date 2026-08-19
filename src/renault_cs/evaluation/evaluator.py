"""统一评估器：验证候选排列并组合 Paint、Ratio 与多目标评分。"""

from __future__ import annotations

from collections import Counter

from renault_cs.domain.enums import ObjectiveKind
from renault_cs.domain.evaluation import EvaluationResult
from renault_cs.domain.models import ProblemInstance
from renault_cs.domain.solution import SequenceSolution
from renault_cs.evaluation.paint import evaluate_paint
from renault_cs.evaluation.ratio import evaluate_ratios
from renault_cs.evaluation.scoring import build_objective_vector, calculate_weighted_score


class RenaultEvaluator:
    """所有算法共享的完整解评估入口。"""

    def __init__(self, *, checker_score_base: int = 100) -> None:
        self._checker_score_base = checker_score_base

    def evaluate(
        self,
        instance: ProblemInstance,
        solution: SequenceSolution,
        *,
        include_details: bool = False,
    ) -> EvaluationResult:
        """先验证车辆集合，再评估可计算的完整候选序列。"""

        validation_errors = self._validate_solution(instance, solution)
        if validation_errors:
            return EvaluationResult(
                is_feasible=False,
                validation_errors=validation_errors,
                paint_changes=0,
                max_paint_batch=0,
                paint_batch_feasible=False,
                hprc_violations=0,
                lprc_violations=0,
            )

        sequence = tuple(instance.vehicle_by_id[ident] for ident in solution.vehicle_ids)
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
        objective_vector = build_objective_vector(
            instance.objectives,
            {
                ObjectiveKind.PAINT_COLOR_CHANGES: paint.changes,
                ObjectiveKind.HPRC_VIOLATIONS: ratios.hprc_violations,
                ObjectiveKind.LPRC_VIOLATIONS: ratios.lprc_violations,
            },
        )

        return EvaluationResult(
            is_feasible=paint.is_batch_feasible,
            validation_errors=(),
            paint_changes=paint.changes,
            max_paint_batch=paint.max_batch,
            paint_batch_feasible=paint.is_batch_feasible,
            hprc_violations=ratios.hprc_violations,
            lprc_violations=ratios.lprc_violations,
            violations_by_constraint=ratios.by_constraint,
            ratio_violation_details=ratios.details if include_details else (),
            paint_batches=paint.batches if include_details else (),
            objective_vector=objective_vector,
            official_score=calculate_weighted_score(
                objective_vector,
                base=self._checker_score_base,
            ),
        )

    @staticmethod
    def _validate_solution(
        instance: ProblemInstance,
        solution: SequenceSolution,
    ) -> tuple[str, ...]:
        errors: list[str] = []
        if solution.instance_name != instance.name:
            errors.append(
                f"Instance name mismatch: expected {instance.name!r}, "
                f"got {solution.instance_name!r}"
            )

        counts = Counter(solution.vehicle_ids)
        duplicates = sorted(ident for ident, count in counts.items() if count > 1)
        unknown = sorted(set(solution.vehicle_ids) - instance.planning_vehicle_ids)
        missing = sorted(instance.planning_vehicle_ids - set(solution.vehicle_ids))
        if duplicates:
            errors.append(f"Duplicate vehicle identifiers: {', '.join(duplicates)}")
        if unknown:
            errors.append(f"Unknown or non-planning vehicle identifiers: {', '.join(unknown)}")
        if missing:
            errors.append(f"Missing planning vehicle identifiers: {', '.join(missing)}")
        return tuple(errors)
