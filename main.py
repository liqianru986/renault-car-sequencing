
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
from pathlib import Path
from time import perf_counter


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from renault_cs.application.config import SolveConfig
from renault_cs.domain.exceptions import RenaultCsError
from renault_cs.evaluation.evaluator import RenaultEvaluator
from renault_cs.exact.gurobi_model import GurobiExactSolver
from renault_cs.exact.model_data import build_exact_model_data
from renault_cs.infrastructure.checker_adapter import WindowsOfficialChecker
from renault_cs.infrastructure.repositories import LocalResultRepository
from renault_cs.infrastructure.roadef_parser import RoadefParser
from renault_cs.infrastructure.solution_io import RoadefSolutionWriter


DEFAULT_INSTANCE = (
    PROJECT_ROOT.parent
    / "Instances_set_X"
    / "Instances_set_X"
    / "028_CH2_EP_ENP_RAF_S51_J1"
)
DEFAULT_CHECKER = PROJECT_ROOT.parent / "checkers" / "WINDOWS" / "exeCarSeq.exe"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="逐阶段运行 Renault Car Sequencing 聚合 MILP。",
    )
    parser.add_argument("--instance", type=Path, default=DEFAULT_INSTANCE)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--checker", type=Path, default=DEFAULT_CHECKER)
    parser.add_argument("--skip-checker", action="store_true")
    parser.add_argument("--time-limit", type=float, default=60.0)
    parser.add_argument("--mip-gap", type=float, default=0.01)
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-mip-start", action="store_true")
    parser.add_argument("--quiet-gurobi", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Gurobi 13 在 Windows 下仍可能无法处理含中文的调试文件路径。
    # 固定工作目录后传入纯英文相对路径，业务输出仍保存在原来的完整目录中。
    os.chdir(PROJECT_ROOT)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_argument_parser().parse_args(argv)
    instance_dir = args.instance.resolve()
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir
        else PROJECT_ROOT / "outputs" / "runs" / instance_dir.name
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = _configure_logging(output_dir / "application.log")
    started_at = perf_counter()

    try:
        logger.info("========== Renault Car Sequencing Exact 调试工作台 ==========")
        logger.info("Python版本: %s", platform.python_version())
        logger.info("项目目录: %s", PROJECT_ROOT)
        logger.info("实例目录: %s", instance_dir)
        logger.info("输出目录: %s", output_dir)

        logger.info("[1/6] 读取并校验官方 instance 文件")
        instance = RoadefParser().parse(instance_dir)
        logger.info(
            "实例=%s | D-1车辆=%d | D车辆=%d | 颜色=%d | HPRC=%d | LPRC=%d",
            instance.name,
            len(instance.previous_day_vehicles),
            len(instance.planning_day_vehicles),
            instance.color_count,
            instance.hprc_count,
            instance.lprc_count,
        )
        logger.info(
            "目标顺序: %s",
            " → ".join(objective.kind.value for objective in instance.objectives),
        )

        logger.info("[2/6] 聚合车辆类型并预计算 MILP 窗口")
        model_data = build_exact_model_data(instance)
        assignment_count = len(model_data.vehicle_types) * model_data.position_count
        logger.info(
            "车辆类型=%d | x[g,t]变量=%d | 颜色变量=%d | Ratio窗口=%d",
            len(model_data.vehicle_types),
            assignment_count,
            len(model_data.colors) * model_data.position_count,
            len(model_data.ratio_windows),
        )
        logger.info(
            "D-1最后颜色=%s | Paint batch limit=%d",
            model_data.previous_day_last_color,
            model_data.paint_batch_limit,
        )

        logger.info("[3/6] 构建并启动 Gurobi 聚合 MILP")
        evaluator = RenaultEvaluator()
        solver = GurobiExactSolver(evaluator)
        gurobi_artifact_dir = Path("outputs") / "runs" / instance_dir.name
        solve_config = SolveConfig(
            time_limit_sec=args.time_limit,
            seed=args.seed,
            algorithm_parameters={
                "mip_gap": args.mip_gap,
                "threads": args.threads,
                "log_to_console": not args.quiet_gurobi,
                "use_mip_start": not args.no_mip_start,
                "gurobi_log_file": gurobi_artifact_dir / "gurobi.log",
                "model_file": gurobi_artifact_dir / "model.lp",
                "mip_start_file": gurobi_artifact_dir / "seqrank_start.mst",
            },
        )
        result = solver.solve(instance, solve_config)
        logger.info("求解状态: %s", result.status)
        logger.info("求解器指标: %s", json.dumps(dict(result.solution.metadata), ensure_ascii=False))
        if result.evaluation is None:
            raise RuntimeError(result.message or "Gurobi没有返回可行解")

        logger.info("[4/6] 使用独立 Evaluator 复评 Gurobi 序列")
        evaluation = result.evaluation
        logger.info(
            "内部评估: feasible=%s | Paint=%d | HPRC=%d | LPRC=%d | Score=%s",
            evaluation.is_feasible,
            evaluation.paint_changes,
            evaluation.hprc_violations,
            evaluation.lprc_violations,
            evaluation.official_score,
        )

        logger.info("[5/6] 写出官方 Sequence rank;Identifier 解文件")
        solution_path = output_dir / "solution.txt"
        RoadefSolutionWriter().write(result.solution, solution_path)
        logger.info("解文件: %s", solution_path)

        checker_payload: dict[str, object] | None = None
        aligned: bool | None = None
        if args.skip_checker:
            logger.info("[6/6] 已按参数跳过官方 Checker")
        else:
            logger.info("[6/6] 调用官方 Checker 并逐项对齐")
            checker = WindowsOfficialChecker(args.checker.resolve())
            report = checker.check(instance_dir, solution_path)
            aligned = (
                report.score == evaluation.official_score
                and report.paint_changes == evaluation.paint_changes
                and report.hprc_violations == evaluation.hprc_violations
                and report.lprc_violations == evaluation.lprc_violations
            )
            checker_payload = {
                "is_valid": report.is_valid,
                "score": report.score,
                "paint_changes": report.paint_changes,
                "hprc_violations": report.hprc_violations,
                "lprc_violations": report.lprc_violations,
            }
            LocalResultRepository(output_dir).save_checker_report(
                "official_checker",
                report.report_text,
            )
            logger.info("官方Checker: %s", json.dumps(checker_payload, ensure_ascii=False))
            logger.info("内部/官方对齐: %s", aligned)

        summary = {
            "status": "ok",
            "instance": instance.name,
            "elapsed_sec": perf_counter() - started_at,
            "model": {
                "planning_vehicles": model_data.position_count,
                "vehicle_types": len(model_data.vehicle_types),
                "assignment_binaries": assignment_count,
                "colors": len(model_data.colors),
                "ratio_windows": len(model_data.ratio_windows),
            },
            "solver": dict(result.solution.metadata),
            "internal_evaluation": {
                "is_feasible": evaluation.is_feasible,
                "paint_changes": evaluation.paint_changes,
                "hprc_violations": evaluation.hprc_violations,
                "lprc_violations": evaluation.lprc_violations,
                "objective_vector": evaluation.objective_vector,
                "official_score": evaluation.official_score,
            },
            "official_checker": checker_payload,
            "checker_aligned": aligned,
            "files": {
                "solution": str(solution_path),
                "model": str(output_dir / "model.lp"),
                "mip_start": str(output_dir / "seqrank_start.mst"),
                "gurobi_log": str(output_dir / "gurobi.log"),
                "application_log": str(output_dir / "application.log"),
            },
        }
        LocalResultRepository(output_dir).save_json("run_summary", summary)
        logger.info("运行摘要: %s", output_dir / "run_summary.json")
        logger.info("==================== 全流程完成 ====================")
        return 0 if aligned is not False else 3
    except (RenaultCsError, ValueError, RuntimeError) as exc:
        logger.error("流程终止: %s", exc)
        return 2
    except Exception:
        logger.exception("未预期错误")
        return 1


def _configure_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("renault_cs.main")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="w")
    file_handler.setFormatter(formatter)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


if __name__ == "__main__":
    raise SystemExit(main())
