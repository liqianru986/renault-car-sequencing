"""应用端口：用 Protocol 规定 Parser、Evaluator、Solver、Writer 和 Checker 的替换边界。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence

from renault_cs.application.config import BenchmarkConfig, SolveConfig
from renault_cs.domain.evaluation import EvaluationResult
from renault_cs.domain.models import ProblemInstance
from renault_cs.domain.solution import SequenceSolution, SolveResult


class InstanceParser(Protocol):
    """将外部场景目录转换为受验证的 ProblemInstance。"""

    def parse(self, instance_dir: Path) -> ProblemInstance:
        """解析一个官方 instance 目录。"""


class SolutionEvaluator(Protocol):
    """对候选排列进行唯一、统一的合法性和目标评估。"""

    def evaluate(
        self,
        instance: ProblemInstance,
        solution: SequenceSolution,
        *,
        include_details: bool = False,
    ) -> EvaluationResult:
        """返回可供算法、Checker 对齐和 Benchmark 共用的评估结果。"""


class SequencingSolver(Protocol):
    """所有 Baseline、启发式和精确求解器必须遵守的统一接口。"""

    @property
    def name(self) -> str:
        """返回稳定的算法名称，用于结果追溯。"""

    def solve(self, instance: ProblemInstance, config: SolveConfig) -> SolveResult:
        """在给定时限和随机种子下求解一个 instance。"""


class SolutionWriter(Protocol):
    """将领域 Solution 序列化为官方解文件格式。"""

    def write(self, solution: SequenceSolution, output_path: Path) -> None:
        """写入 Sequence rank;Identifier 文件。"""


class CheckerResult(Protocol):
    """官方 Checker 结果的只读视图。"""

    @property
    def is_valid(self) -> bool:
        """官方 Checker 是否接受该解。"""

    @property
    def score(self) -> int:
        """官方加权分。"""

    @property
    def paint_changes(self) -> int:
        """官方 Paint changes。"""

    @property
    def hprc_violations(self) -> int:
        """官方 HPRC 违反量。"""

    @property
    def lprc_violations(self) -> int:
        """官方 LPRC 违反量。"""

    @property
    def report_text(self) -> str:
        """官方原始报告。"""


class OfficialChecker(Protocol):
    """隔离历史 Checker 可执行程序、工作目录和报告格式。"""

    def check(self, instance_dir: Path, solution_file: Path) -> CheckerResult:
        """运行 Checker 并返回结构化结果。"""


class BenchmarkRunnerPort(Protocol):
    """批量实验编排边界，便于 CLI 与未来其他前端复用。"""

    def run(
        self,
        instance_dirs: Sequence[Path],
        solvers: Sequence[SequencingSolver],
        config: BenchmarkConfig,
        *,
        dataset: str,
    ) -> object:
        """运行统一时限和种子集合的批量实验。"""
