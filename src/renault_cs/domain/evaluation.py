"""评估数据模型：表达颜色批次、滑动窗口违反和统一目标结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from renault_cs.domain.exceptions import DomainValidationError


@dataclass(frozen=True, slots=True)
class PaintBatch:
    """一个连续同色车辆批次，位置使用 D 日解序列的0基索引。"""

    color: str
    start_position: int
    end_position: int
    length: int

    def __post_init__(self) -> None:
        if self.start_position < 0 or self.end_position < self.start_position:
            raise DomainValidationError("Invalid PaintBatch position range")
        if self.length != self.end_position - self.start_position + 1:
            raise DomainValidationError("PaintBatch.length does not match its position range")


@dataclass(frozen=True, slots=True)
class ConstraintViolation:
    """一条 N/P 约束的违反诊断；位置以 D 首车为 0，D-1 位置为负。"""

    constraint_id: str
    window_start: int
    window_end: int
    observed_count: int
    allowed_count: int
    violation_count: int
    crosses_day_boundary: bool

    def __post_init__(self) -> None:
        if self.window_end < self.window_start:
            raise DomainValidationError("Invalid violation window range")
        if self.observed_count < 0 or self.allowed_count < 0 or self.violation_count <= 0:
            raise DomainValidationError("ConstraintViolation counts are inconsistent")


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """所有算法和 Benchmark 共享的标准评估输出。"""

    is_feasible: bool
    validation_errors: tuple[str, ...]
    paint_changes: int
    max_paint_batch: int
    paint_batch_feasible: bool
    hprc_violations: int
    lprc_violations: int
    violations_by_constraint: Mapping[str, int] = field(default_factory=dict)
    ratio_violation_details: tuple[ConstraintViolation, ...] = ()
    paint_batches: tuple[PaintBatch, ...] = ()
    objective_vector: tuple[int, ...] = ()
    official_score: int | None = None

    def __post_init__(self) -> None:
        counts = (
            self.paint_changes,
            self.max_paint_batch,
            self.hprc_violations,
            self.lprc_violations,
        )
        if any(value < 0 for value in counts):
            raise DomainValidationError("Evaluation counts cannot be negative")
        object.__setattr__(
            self,
            "violations_by_constraint",
            MappingProxyType(dict(self.violations_by_constraint)),
        )
