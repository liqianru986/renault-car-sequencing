"""求解结果模型：隔离纯车辆序列、算法运行元数据和最终评估结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping

from renault_cs.domain.exceptions import DomainValidationError

if TYPE_CHECKING:
    from renault_cs.domain.evaluation import EvaluationResult


@dataclass(frozen=True, slots=True)
class SequenceSolution:
    """仅包含 D 日车辆的候选排列及其可追溯运行元数据。"""

    instance_name: str
    vehicle_ids: tuple[str, ...]
    algorithm: str
    runtime_sec: float = 0.0
    seed: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.instance_name:
            raise DomainValidationError("SequenceSolution.instance_name cannot be empty")
        if not self.algorithm:
            raise DomainValidationError("SequenceSolution.algorithm cannot be empty")
        if self.runtime_sec < 0:
            raise DomainValidationError("SequenceSolution.runtime_sec cannot be negative")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class SolveResult:
    """一次求解任务的标准返回对象。"""

    solution: SequenceSolution
    evaluation: EvaluationResult | None
    status: str
    message: str | None = None

