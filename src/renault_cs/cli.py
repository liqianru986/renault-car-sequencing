"""项目命令行入口：提供 inspect、solve、evaluate、check 和 benchmark。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from renault_cs.algorithms.alns import AlnsSolver
from renault_cs.algorithms.seqrank import SeqRankSolver
from renault_cs.application.config import BenchmarkConfig, SolveConfig
from renault_cs.application.inspect_instance import inspect_instance
from renault_cs.application.run_benchmark import run_benchmark
from renault_cs.application.ports import CheckerResult
from renault_cs.application.ports import SequencingSolver
from renault_cs.application.solve_instance import solve_instance
from renault_cs.benchmark.runner import BenchmarkRunner
from renault_cs.domain.evaluation import EvaluationResult
from renault_cs.domain.exceptions import RenaultCsError
from renault_cs.evaluation.evaluator import RenaultEvaluator
from renault_cs.exact.gurobi_model import GurobiExactSolver
from renault_cs.infrastructure.checker_adapter import WindowsOfficialChecker
from renault_cs.infrastructure.repositories import LocalResultRepository
from renault_cs.infrastructure.roadef_parser import RoadefParser
from renault_cs.infrastructure.solution_io import RoadefSolutionReader, RoadefSolutionWriter


def build_parser() -> argparse.ArgumentParser:
    """构建无全局状态的 CLI 参数解析器。"""

    parser = argparse.ArgumentParser(prog="renault-cs")
    subcommands = parser.add_subparsers(dest="command", required=True)

    inspect_command = subcommands.add_parser("inspect", help="查看实例规模与目标顺序")
    inspect_command.add_argument("--instance", type=Path, required=True)

    solve_command = subcommands.add_parser("solve", help="求解单个实例并输出官方解文件")
    solve_command.add_argument("--instance", type=Path, required=True)
    solve_command.add_argument("--output", type=Path, required=True)
    solve_command.add_argument(
        "--algorithm", choices=("seqrank", "alns", "gurobi_exact"), default="seqrank"
    )
    solve_command.add_argument("--time-limit", type=float, default=60.0)
    solve_command.add_argument("--max-iterations", type=int)
    solve_command.add_argument("--seed", type=int, default=42)
    solve_command.add_argument("--checker", type=Path)
    _add_exact_arguments(solve_command)
    _add_alns_arguments(solve_command)

    evaluate_command = subcommands.add_parser("evaluate", help="使用内部 Evaluator 评估解文件")
    evaluate_command.add_argument("--instance", type=Path, required=True)
    evaluate_command.add_argument("--solution", type=Path, required=True)
    evaluate_command.add_argument("--details", action="store_true")

    check_command = subcommands.add_parser("check", help="对比内部评估与官方 Checker")
    check_command.add_argument("--instance", type=Path, required=True)
    check_command.add_argument("--solution", type=Path, required=True)
    check_command.add_argument("--checker", type=Path, required=True)

    benchmark_command = subcommands.add_parser("benchmark", help="批量运行数据集")
    benchmark_command.add_argument("--dataset", type=Path, required=True)
    benchmark_command.add_argument("--output-dir", type=Path, required=True)
    benchmark_command.add_argument(
        "--algorithm", choices=("seqrank", "alns", "gurobi_exact"), default="seqrank"
    )
    benchmark_command.add_argument("--time-limit", type=float, default=60.0)
    benchmark_command.add_argument("--seeds", type=int, nargs="+", default=[42])
    benchmark_command.add_argument("--checker", type=Path)
    benchmark_command.add_argument("--discard-solutions", action="store_true")
    _add_exact_arguments(benchmark_command)
    _add_alns_arguments(benchmark_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """执行子命令；业务异常以简洁 JSON 返回并使用非零退出码。"""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    try:
        payload = _dispatch(args)
    except (RenaultCsError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def _dispatch(args: argparse.Namespace) -> dict[str, object]:
    parser = RoadefParser()
    evaluator = RenaultEvaluator()
    writer = RoadefSolutionWriter()

    if args.command == "inspect":
        return {"status": "ok", **inspect_instance(args.instance, parser).to_dict()}

    if args.command == "solve":
        solver = _make_solver(args.algorithm, evaluator)
        checker = WindowsOfficialChecker(args.checker) if args.checker else None
        execution = solve_instance(
            args.instance,
            args.output,
            SolveConfig(
                time_limit_sec=args.time_limit,
                seed=args.seed,
                max_iterations=args.max_iterations,
                algorithm_parameters={**_exact_parameters(args), **_alns_parameters(args)},
            ),
            parser=parser,
            solver=solver,
            writer=writer,
            checker=checker,
        )
        evaluation = execution.result.evaluation
        assert evaluation is not None
        return {
            "status": "ok",
            "instance": execution.result.solution.instance_name,
            "algorithm": execution.result.solution.algorithm,
            "solve_status": execution.result.status,
            "solution_path": str(execution.solution_path.resolve()),
            "runtime_sec": execution.result.solution.runtime_sec,
            "solver_metadata": dict(execution.result.solution.metadata),
            "evaluation": _evaluation_payload(evaluation),
            "checker": (
                _checker_payload(execution.checker_report)
                if execution.checker_report is not None
                else None
            ),
        }

    if args.command == "benchmark":
        repository = LocalResultRepository(args.output_dir)
        checker = WindowsOfficialChecker(args.checker) if args.checker else None
        config = BenchmarkConfig(
            time_limit_sec=args.time_limit,
            seeds=tuple(args.seeds),
            output_dir=args.output_dir,
            run_official_checker=checker is not None,
            keep_all_solutions=not args.discard_solutions,
            algorithm_parameters={**_exact_parameters(args), **_alns_parameters(args)},
        )
        runner = BenchmarkRunner(
            parser=parser,
            writer=writer,
            repository=repository,
            checker=checker,
        )
        records = run_benchmark(
            args.dataset,
            [_make_solver(args.algorithm, evaluator)],
            config,
            runner=runner,
            repository=repository,
        )
        return {
            "status": "ok",
            "runs": len(records),
            "records_path": str((args.output_dir / "benchmark_records.csv").resolve()),
            "summary_path": str((args.output_dir / "benchmark_summary.json").resolve()),
        }

    instance = parser.parse(args.instance)
    solution = RoadefSolutionReader().read(
        args.solution,
        instance_name=instance.name,
        algorithm="external",
    )
    internal = evaluator.evaluate(
        instance,
        solution,
        include_details=getattr(args, "details", False),
    )

    if args.command == "evaluate":
        return {"status": "ok", "evaluation": _evaluation_payload(internal)}

    if args.command == "check":
        official = WindowsOfficialChecker(args.checker).check(args.instance, args.solution)
        aligned = (
            official.paint_changes == internal.paint_changes
            and official.hprc_violations == internal.hprc_violations
            and official.lprc_violations == internal.lprc_violations
            and official.score == internal.official_score
        )
        return {
            "status": "ok" if aligned else "mismatch",
            "aligned": aligned,
            "internal": _evaluation_payload(internal),
            "official": _checker_payload(official),
        }

    raise ValueError(f"Unsupported command: {args.command}")


def _make_solver(name: str, evaluator: RenaultEvaluator) -> SequencingSolver:
    if name == "seqrank":
        return SeqRankSolver(evaluator)
    if name == "alns":
        return AlnsSolver(evaluator)
    if name == "gurobi_exact":
        return GurobiExactSolver(evaluator)
    raise ValueError(f"Algorithm is not implemented yet: {name}")


def _add_exact_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--mip-gap", type=float, default=0.0)
    command.add_argument("--threads", type=int, default=0)
    command.add_argument("--quiet-solver", action="store_true")
    command.add_argument("--no-mip-start", action="store_true")


def _add_alns_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--destroy-fraction", type=float, default=0.12)
    command.add_argument("--segment-length", type=int, default=50)
    command.add_argument("--reaction", type=float, default=0.20)
    command.add_argument("--cooling", type=float, default=0.995)
    command.add_argument("--candidate-limit", type=int, default=30)
    command.add_argument("--max-destroy-count", type=int, default=8)
    command.add_argument("--regret-sample-size", type=int, default=6)
    command.add_argument("--local-search-trials", type=int, default=20)


def _exact_parameters(args: argparse.Namespace) -> dict[str, object]:
    return {
        "mip_gap": args.mip_gap,
        "threads": args.threads,
        "log_to_console": not args.quiet_solver,
        "use_mip_start": not args.no_mip_start,
    }


def _alns_parameters(args: argparse.Namespace) -> dict[str, object]:
    return {
        "destroy_fraction": args.destroy_fraction,
        "segment_length": args.segment_length,
        "reaction": args.reaction,
        "cooling": args.cooling,
        "candidate_limit": args.candidate_limit,
        "max_destroy_count": args.max_destroy_count,
        "regret_sample_size": args.regret_sample_size,
        "local_search_trials": args.local_search_trials,
    }


def _evaluation_payload(evaluation: EvaluationResult) -> dict[str, object]:
    return {
        "is_feasible": evaluation.is_feasible,
        "validation_errors": evaluation.validation_errors,
        "paint_changes": evaluation.paint_changes,
        "max_paint_batch": evaluation.max_paint_batch,
        "paint_batch_feasible": evaluation.paint_batch_feasible,
        "hprc_violations": evaluation.hprc_violations,
        "lprc_violations": evaluation.lprc_violations,
        "violations_by_constraint": dict(evaluation.violations_by_constraint),
        "ratio_violation_details": [asdict(item) for item in evaluation.ratio_violation_details],
        "paint_batches": [asdict(item) for item in evaluation.paint_batches],
        "objective_vector": evaluation.objective_vector,
        "official_score": evaluation.official_score,
    }


def _checker_payload(report: CheckerResult) -> dict[str, object]:
    return {
        "is_valid": report.is_valid,
        "score": report.score,
        "paint_changes": report.paint_changes,
        "hprc_violations": report.hprc_violations,
        "lprc_violations": report.lprc_violations,
    }


if __name__ == "__main__":
    raise SystemExit(main())
