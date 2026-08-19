"""领域层出口：集中导出与文件格式、算法和求解器无关的核心类型。"""

from renault_cs.domain.enums import HprcDifficulty, ObjectiveKind, RatioPriority
from renault_cs.domain.evaluation import ConstraintViolation, EvaluationResult, PaintBatch
from renault_cs.domain.models import ObjectiveSpec, ProblemInstance, RatioConstraint, Vehicle
from renault_cs.domain.solution import SequenceSolution, SolveResult

__all__ = [
    "ConstraintViolation",
    "EvaluationResult",
    "HprcDifficulty",
    "ObjectiveKind",
    "ObjectiveSpec",
    "PaintBatch",
    "ProblemInstance",
    "RatioConstraint",
    "RatioPriority",
    "SequenceSolution",
    "SolveResult",
    "Vehicle",
]

