"""比例约束评估：统计跨日窗口及 D 日尾部缩短窗口的违反量。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from renault_cs.domain.evaluation import ConstraintViolation
from renault_cs.domain.models import RatioConstraint, Vehicle


@dataclass(frozen=True, slots=True)
class RatioEvaluation:
    """全部 N/P 约束的内部汇总结果。"""

    hprc_violations: int
    lprc_violations: int
    by_constraint: dict[str, int]
    details: tuple[ConstraintViolation, ...]


def evaluate_ratios(
    previous_day: Sequence[Vehicle],
    planning_sequence: Sequence[Vehicle],
    constraints: Sequence[RatioConstraint],
) -> RatioEvaluation:
    """按官方规则累计每个窗口中 ``max(0, observed - N)``。"""

    previous_count = len(previous_day)
    combined = tuple(previous_day) + tuple(planning_sequence)
    hprc_total = 0
    lprc_total = 0
    by_constraint: dict[str, int] = {}
    details: list[ConstraintViolation] = []

    for constraint_index, constraint in enumerate(constraints):
        flags = [int(vehicle.option_flags[constraint_index]) for vehicle in combined]
        # start_position 使用 D 日坐标：D 第一辆为 0，D-1 车辆为负数。
        first_start = max(-previous_count, -(constraint.denominator - 1))
        constraint_total = 0

        for start_position in range(first_start, len(planning_sequence)):
            combined_start = previous_count + start_position
            combined_end = min(combined_start + constraint.denominator, len(combined))
            observed = sum(flags[combined_start:combined_end])
            excess = max(0, observed - constraint.numerator)
            if excess == 0:
                continue

            actual_end = start_position + (combined_end - combined_start) - 1
            constraint_total += excess
            details.append(
                ConstraintViolation(
                    constraint_id=constraint.ident,
                    window_start=start_position,
                    window_end=actual_end,
                    observed_count=observed,
                    allowed_count=constraint.numerator,
                    violation_count=excess,
                    crosses_day_boundary=start_position < 0 <= actual_end,
                )
            )

        by_constraint[constraint.ident] = constraint_total
        if constraint.is_high_priority:
            hprc_total += constraint_total
        else:
            lprc_total += constraint_total

    return RatioEvaluation(
        hprc_violations=hprc_total,
        lprc_violations=lprc_total,
        by_constraint=by_constraint,
        details=tuple(details),
    )
