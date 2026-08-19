"""单实例求解用例：编排 Parser、Solver、Writer、Repository 和 Checker。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from renault_cs.application.config import SolveConfig
from renault_cs.application.ports import (
    CheckerResult,
    InstanceParser,
    OfficialChecker,
    SequencingSolver,
    SolutionWriter,
)
from renault_cs.domain.solution import SolveResult


@dataclass(frozen=True, slots=True)
class SolveExecution:
    """一次端到端求解产生的领域结果与外部产物。"""

    result: SolveResult
    solution_path: Path
    checker_report: CheckerResult | None = None


def solve_instance(
    instance_dir: Path,
    output_path: Path,
    config: SolveConfig,
    *,
    parser: InstanceParser,
    solver: SequencingSolver,
    writer: SolutionWriter,
    checker: OfficialChecker | None = None,
) -> SolveExecution:
    """执行单实例完整链路，并在写文件前拒绝结构非法解。"""

    instance = parser.parse(instance_dir)
    result = solver.solve(instance, config)
    if result.evaluation is None or result.evaluation.validation_errors:
        errors = result.evaluation.validation_errors if result.evaluation else (result.status,)
        raise ValueError(f"Solver returned an invalid result: {'; '.join(errors)}")

    writer.write(result.solution, output_path)
    checker_report = checker.check(instance_dir, output_path) if checker is not None else None
    return SolveExecution(
        result=result,
        solution_path=Path(output_path),
        checker_report=checker_report,
    )
