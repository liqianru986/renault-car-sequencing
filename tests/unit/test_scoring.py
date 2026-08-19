"""多目标评分测试：覆盖动态目标顺序与 Checker 位权。"""

from renault_cs.domain.enums import HprcDifficulty, ObjectiveKind
from renault_cs.domain.models import ObjectiveSpec
from renault_cs.evaluation.scoring import build_objective_vector, calculate_weighted_score


def test_scoring_follows_instance_objective_rank() -> None:
    objectives = (
        ObjectiveSpec(1, ObjectiveKind.HPRC_VIOLATIONS, "h", HprcDifficulty.EASY),
        ObjectiveSpec(2, ObjectiveKind.PAINT_COLOR_CHANGES, "p"),
        ObjectiveSpec(3, ObjectiveKind.LPRC_VIOLATIONS, "l"),
    )
    vector = build_objective_vector(
        objectives,
        {
            ObjectiveKind.PAINT_COLOR_CHANGES: 132,
            ObjectiveKind.HPRC_VIOLATIONS: 0,
            ObjectiveKind.LPRC_VIOLATIONS: 99,
        },
    )

    assert vector == (0, 132, 99)
    assert calculate_weighted_score(vector, base=100) == 13_299


def test_scoring_keeps_three_checker_weight_slots_for_two_objectives() -> None:
    assert calculate_weighted_score((6, 10), base=100) == 61_000
