"""Ratio 评估测试：覆盖跨日窗口、尾部缩短窗口和分项汇总。"""

from renault_cs.domain.enums import RatioPriority
from renault_cs.domain.models import RatioConstraint, Vehicle
from renault_cs.evaluation.ratio import evaluate_ratios


def _vehicle(ident: str, enabled: bool) -> Vehicle:
    return Vehicle(ident, "D", 1, "1", (enabled,))


def test_ratio_counts_cross_day_and_shortened_tail_windows() -> None:
    constraint = RatioConstraint("HPRC1", 1, 3, RatioPriority.HIGH)
    previous = (_vehicle("P1", False), _vehicle("P2", True))
    planning = (_vehicle("A", True), _vehicle("B", False), _vehicle("C", True))

    result = evaluate_ratios(previous, planning, (constraint,))

    # 窗口起点 -2、-1、0、1、2 的违反量依次为 1、1、1、0、0。
    assert result.hprc_violations == 3
    assert result.lprc_violations == 0
    assert result.by_constraint == {"HPRC1": 3}
    assert result.details[0].crosses_day_boundary is True
