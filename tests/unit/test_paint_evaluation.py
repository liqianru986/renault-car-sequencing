"""Paint 评估测试：覆盖跨日颜色切换、批次统计和硬上限。"""

from renault_cs.domain.models import Vehicle
from renault_cs.evaluation.paint import evaluate_paint


def _vehicle(ident: str, color: str) -> Vehicle:
    return Vehicle(ident, "D", 1, color, ())


def test_paint_counts_boundary_changes_and_batches() -> None:
    previous = (_vehicle("P", "BLUE"),)
    planning = (
        _vehicle("A", "RED"),
        _vehicle("B", "RED"),
        _vehicle("C", "BLUE"),
    )

    result = evaluate_paint(previous, planning, batch_limit=2)

    assert result.changes == 2  # D-1→D 一次，D 内部一次。
    assert result.max_batch == 2
    assert result.is_batch_feasible is True
    assert [(batch.color, batch.length) for batch in result.batches] == [
        ("RED", 2),
        ("BLUE", 1),
    ]


def test_paint_detects_batch_limit_violation() -> None:
    planning = tuple(_vehicle(str(index), "RED") for index in range(3))

    assert evaluate_paint((), planning, batch_limit=2).is_batch_feasible is False
