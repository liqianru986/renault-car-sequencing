"""Benchmark Runner：统一运行实例、算法、种子并持久化原子记录。"""

from __future__ import annotations

from pathlib import Path

from renault_cs.application.config import BenchmarkConfig, SolveConfig
from renault_cs.application.ports import InstanceParser, OfficialChecker, SequencingSolver, SolutionWriter
from renault_cs.benchmark.records import BenchmarkRecord
from renault_cs.domain.enums import ObjectiveKind
from renault_cs.infrastructure.repositories import LocalResultRepository


class BenchmarkRunner:
    """批量实验执行器；不包含任何算法特定逻辑。"""

    def __init__(
        self,
        *,
        parser: InstanceParser,
        writer: SolutionWriter,
        repository: LocalResultRepository,
        checker: OfficialChecker | None = None,
    ) -> None:
        self._parser = parser
        self._writer = writer
        self._repository = repository
        self._checker = checker

    def run(
        self,
        instance_dirs: list[Path],
        solvers: list[SequencingSolver],
        config: BenchmarkConfig,
        *,
        dataset: str,
    ) -> list[BenchmarkRecord]:
        """顺序运行基础版本；并行策略留到算法稳定后再引入。"""

        records: list[BenchmarkRecord] = []
        for instance_dir in sorted(map(Path, instance_dirs), key=lambda path: path.name):
            instance = self._parser.parse(instance_dir)
            for solver in solvers:
                for seed in config.seeds:
                    solve_config = SolveConfig(
                        time_limit_sec=config.time_limit_sec,
                        seed=seed,
                        algorithm_parameters=config.algorithm_parameters,
                    )
                    result = solver.solve(instance, solve_config)
                    if result.evaluation is None:
                        raise RuntimeError(
                            f"{solver.name} returned no evaluation for {instance.name}"
                        )
                    evaluation = result.evaluation
                    solution_path = self._repository.solution_path(
                        instance.name,
                        solver.name,
                        seed,
                    )
                    self._writer.write(result.solution, solution_path)

                    checker_passed: bool | None = None
                    if config.run_official_checker:
                        if self._checker is None:
                            raise ValueError("run_official_checker=True but no checker was configured")
                        report = self._checker.check(instance_dir, solution_path)
                        checker_passed = (
                            report.is_valid
                            and report.paint_changes == evaluation.paint_changes
                            and report.hprc_violations == evaluation.hprc_violations
                            and report.lprc_violations == evaluation.lprc_violations
                            and report.score == evaluation.official_score
                        )
                        self._repository.save_checker_report(
                            f"{instance.name}_{solver.name}_seed-{seed}",
                            report.report_text,
                        )

                    hprc_objective = next(
                        (
                            objective
                            for objective in instance.objectives
                            if objective.kind is ObjectiveKind.HPRC_VIOLATIONS
                        ),
                        None,
                    )
                    scenario_class = (
                        hprc_objective.hprc_difficulty.value
                        if hprc_objective and hprc_objective.hprc_difficulty
                        else "not_applicable"
                    )
                    records.append(
                        BenchmarkRecord(
                            dataset=dataset,
                            instance_name=instance.name,
                            scenario_class=scenario_class,
                            vehicle_count=len(instance.planning_day_vehicles),
                            color_count=instance.color_count,
                            hprc_count=instance.hprc_count,
                            lprc_count=instance.lprc_count,
                            algorithm=solver.name,
                            seed=seed,
                            time_limit_sec=config.time_limit_sec,
                            runtime_sec=result.solution.runtime_sec,
                            feasible=evaluation.is_feasible,
                            checker_passed=checker_passed,
                            paint_changes=evaluation.paint_changes,
                            hprc_violations=evaluation.hprc_violations,
                            lprc_violations=evaluation.lprc_violations,
                            objective_vector=evaluation.objective_vector,
                            official_score=evaluation.official_score,
                            best_bound=_optional_float(
                                result.solution.metadata.get("best_bound")
                            ),
                            mip_gap=_optional_float(result.solution.metadata.get("mip_gap")),
                            node_count=_optional_float(
                                result.solution.metadata.get("node_count")
                            ),
                            best_found_time_sec=result.solution.runtime_sec,
                        )
                    )

                    if not config.keep_all_solutions:
                        solution_path.unlink(missing_ok=True)

        self._repository.save_benchmark_records(records)
        return records


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None
