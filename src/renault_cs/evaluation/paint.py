"""涂装评估：计算跨日颜色切换、D 日颜色批次及批次上限。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from renault_cs.domain.evaluation import PaintBatch
from renault_cs.domain.models import Vehicle


@dataclass(frozen=True, slots=True)
class PaintEvaluation:
    """涂装目标与硬约束的内部计算结果。"""

    changes: int
    max_batch: int
    is_batch_feasible: bool
    batches: tuple[PaintBatch, ...]


def evaluate_paint(
    previous_day: Sequence[Vehicle],
    planning_sequence: Sequence[Vehicle],
    batch_limit: int,
) -> PaintEvaluation:
    """评估 D 日序列；D-1 最后一辆车只参与边界颜色切换计数。"""

    if not planning_sequence:
        return PaintEvaluation(changes=0, max_batch=0, is_batch_feasible=True, batches=())

    changes = int(
        bool(previous_day)
        and previous_day[-1].paint_color != planning_sequence[0].paint_color
    )
    batches: list[PaintBatch] = []
    batch_start = 0

    for position in range(1, len(planning_sequence)):
        if planning_sequence[position].paint_color == planning_sequence[position - 1].paint_color:
            continue
        changes += 1
        batches.append(_make_batch(planning_sequence, batch_start, position - 1))
        batch_start = position

    batches.append(_make_batch(planning_sequence, batch_start, len(planning_sequence) - 1))
    max_batch = max(batch.length for batch in batches)
    return PaintEvaluation(
        changes=changes,
        max_batch=max_batch,
        is_batch_feasible=max_batch <= batch_limit,
        batches=tuple(batches),
    )


def _make_batch(sequence: Sequence[Vehicle], start: int, end: int) -> PaintBatch:
    return PaintBatch(
        color=sequence[start].paint_color,
        start_position=start,
        end_position=end,
        length=end - start + 1,
    )
