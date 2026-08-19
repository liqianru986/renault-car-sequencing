"""精确求解公共类型：统一状态、边界、Gap 与运行统计。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExactSolveStatus(str, Enum):
    """与具体求解器状态码解耦的标准状态。"""

    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    UNBOUNDED = "unbounded"
    NO_SOLUTION = "no_solution"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class ExactSolveMetrics:
    """用于 Benchmark 和诊断的精确求解统计。"""

    status: ExactSolveStatus
    objective_value: float | None
    best_bound: float | None
    mip_gap: float | None
    node_count: float
    solution_count: int
    runtime_sec: float
