"""SeqRank Baseline：按 Renault 原始 SeqRank 生成可追溯基线解。"""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

from renault_cs.application.config import SolveConfig
from renault_cs.domain.models import ProblemInstance
from renault_cs.domain.solution import SequenceSolution, SolveResult

if TYPE_CHECKING:
    from renault_cs.application.ports import SolutionEvaluator


class SeqRankSolver:
    """保持 Renault 原始 D 日顺序的确定性基线求解器。"""

    def __init__(self, evaluator: SolutionEvaluator) -> None:
        self._evaluator = evaluator

    @property
    def name(self) -> str:
        """返回用于日志和 Benchmark 的稳定算法名称。"""

        return "seqrank"

    def solve(self, instance: ProblemInstance, config: SolveConfig) -> SolveResult:
        """按 ``original_rank`` 排列 D 日车辆并立即使用统一评估器评分。"""

        started_at = perf_counter()
        ordered_vehicles = sorted(
            instance.planning_day_vehicles,
            key=lambda vehicle: (vehicle.original_rank, vehicle.ident),
        )
        runtime_sec = perf_counter() - started_at
        solution = SequenceSolution(
            instance_name=instance.name,
            vehicle_ids=tuple(vehicle.ident for vehicle in ordered_vehicles),
            algorithm=self.name,
            runtime_sec=runtime_sec,
            seed=None,
            metadata={
                "is_deterministic": True,
                "time_limit_sec": config.time_limit_sec,
            },
        )
        evaluation = self._evaluator.evaluate(instance, solution)
        return SolveResult(
            solution=solution,
            evaluation=evaluation,
            status="completed",
        )
