"""直接点击 Run：批量比较官方初始序列、Gurobi 与 ALNS。"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from time import perf_counter


# ======================== 只需要修改这里的参数 ========================
PROJECT_ROOT = Path(__file__).resolve().parent
DATASETS = {
    "Set A": PROJECT_ROOT.parent / "Instances_set_A" / "Instances",
    "Set X": PROJECT_ROOT.parent / "Instances_set_X" / "Instances_set_X",
}
CHECKER_PATH = PROJECT_ROOT.parent / "checkers" / "WINDOWS" / "exeCarSeq.exe"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "all_instances_comparison_60s_final_v3"

GUROBI_TIME_LIMIT_SEC = 60.0
ALNS_TIME_LIMIT_SEC = 60.0
ALNS_SEED = 42
GUROBI_MIP_GAP = 0.01
GUROBI_THREADS = 0
ALNS_DESTROY_FRACTION = 0.12
ALNS_SEGMENT_LENGTH = 50
ALNS_CANDIDATE_LIMIT = 30
ALNS_MAX_DESTROY_COUNT = 8
ALNS_REGRET_SAMPLE_SIZE = 6
ALNS_LOCAL_SEARCH_TRIALS = 20
ALNS_PAINT_SEARCH_TRIALS = 20
ALNS_VFLS_TRIALS = 100
ALNS_VFLS_INTERVAL = 10
# 调试时可改为 1；正式全量运行保持 None。
MAX_INSTANCES: int | None = None
RESUME_EXISTING = True
# =====================================================================


SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from renault_cs.algorithms.alns import AlnsSolver
from renault_cs.algorithms.seqrank import SeqRankSolver
from renault_cs.application.config import SolveConfig
from renault_cs.evaluation.evaluator import RenaultEvaluator
from renault_cs.exact.gurobi_model import GurobiExactSolver
from renault_cs.infrastructure.checker_adapter import WindowsOfficialChecker
from renault_cs.infrastructure.roadef_parser import RoadefParser
from renault_cs.infrastructure.solution_io import RoadefSolutionWriter


COLUMNS = (
    "数据集",
    "实例名",
    "车数",
    "官方初始总分",
    "官方初始HPRC",
    "官方初始LPRC",
    "官方初始Paint",
    "Gurobi状态",
    "Gurobi总分",
    "GurobiHPRC",
    "GurobiLPRC",
    "GurobiPaint",
    "Gurobi运行秒数",
    "Gurobi Best Bound",
    "Gurobi MIP Gap",
    "ALNS总分",
    "ALNS HPRC",
    "ALNS LPRC",
    "ALNS Paint",
    "ALNS运行秒数",
    "ALNS迭代数",
    "三种解官方Checker通过",
    "错误信息",
)


def main() -> None:
    """遍历全部实例，逐实例保存比较结果。"""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    solution_dir = OUTPUT_DIR / "solutions"
    solution_dir.mkdir(exist_ok=True)
    csv_path = OUTPUT_DIR / "all_instances_results.csv"

    parser = RoadefParser()
    evaluator = RenaultEvaluator()
    writer = RoadefSolutionWriter()
    checker = WindowsOfficialChecker(CHECKER_PATH)
    solvers = {
        "seqrank": SeqRankSolver(evaluator),
        "gurobi": GurobiExactSolver(evaluator),
        "alns": AlnsSolver(evaluator),
    }

    jobs = [
        (dataset_name, instance_dir)
        for dataset_name, dataset_dir in DATASETS.items()
        for instance_dir in sorted(dataset_dir.iterdir(), key=lambda path: path.name)
        if instance_dir.is_dir()
    ]
    if MAX_INSTANCES is not None:
        jobs = jobs[:MAX_INSTANCES]
    rows = _load_existing_rows(csv_path) if RESUME_EXISTING else []
    completed = {
        (row.get("数据集", ""), row.get("实例名", ""))
        for row in rows
        if row.get("ALNS总分") and row.get("Gurobi状态")
    }
    total_started = perf_counter()

    print(f"共发现 {len(jobs)} 个实例，结果将保存到：{csv_path}")
    for number, (dataset_name, instance_dir) in enumerate(jobs, start=1):
        if (dataset_name, instance_dir.name) in completed:
            print(f"\n[{number}/{len(jobs)}] {dataset_name} / {instance_dir.name}：已完成，跳过")
            continue
        print(f"\n[{number}/{len(jobs)}] {dataset_name} / {instance_dir.name}")
        row: dict[str, object] = {
            "数据集": dataset_name,
            "实例名": instance_dir.name,
            "错误信息": "",
        }
        errors: list[str] = []

        try:
            instance = parser.parse(instance_dir)
            row["车数"] = len(instance.planning_day_vehicles)

            initial = solvers["seqrank"].solve(
                instance,
                SolveConfig(time_limit_sec=1.0),
            )
            _record_result(row, "官方初始", initial)
            initial_ok = _write_and_check(
                writer, checker, instance_dir, solution_dir, initial, "seqrank"
            )
            print(f"  初始序列：{initial.evaluation.official_score}")

            gurobi = solvers["gurobi"].solve(
                instance,
                SolveConfig(
                    time_limit_sec=GUROBI_TIME_LIMIT_SEC,
                    seed=ALNS_SEED,
                    algorithm_parameters={
                        "mip_gap": GUROBI_MIP_GAP,
                        "threads": GUROBI_THREADS,
                        "log_to_console": False,
                        "use_mip_start": True,
                    },
                ),
            )
            row["Gurobi状态"] = gurobi.status
            if gurobi.evaluation is not None:
                _record_result(row, "Gurobi", gurobi)
                gurobi_ok = _write_and_check(
                    writer, checker, instance_dir, solution_dir, gurobi, "gurobi"
                )
                print(f"  Gurobi：{gurobi.evaluation.official_score} ({gurobi.status})")
            else:
                gurobi_ok = False
                errors.append(f"Gurobi: {gurobi.message or '无可行解'}")
                print(f"  Gurobi：无可行解 ({gurobi.status})")

            alns = solvers["alns"].solve(
                instance,
                SolveConfig(
                    time_limit_sec=ALNS_TIME_LIMIT_SEC,
                    seed=ALNS_SEED,
                    algorithm_parameters={
                        "destroy_fraction": ALNS_DESTROY_FRACTION,
                        "segment_length": ALNS_SEGMENT_LENGTH,
                        "candidate_limit": ALNS_CANDIDATE_LIMIT,
                        "max_destroy_count": ALNS_MAX_DESTROY_COUNT,
                        "regret_sample_size": ALNS_REGRET_SAMPLE_SIZE,
                        "local_search_trials": ALNS_LOCAL_SEARCH_TRIALS,
                        "paint_search_trials": ALNS_PAINT_SEARCH_TRIALS,
                        "vfls_trials": ALNS_VFLS_TRIALS,
                        "vfls_interval": ALNS_VFLS_INTERVAL,
                    },
                ),
            )
            _record_result(row, "ALNS", alns)
            alns_ok = _write_and_check(
                writer, checker, instance_dir, solution_dir, alns, "alns"
            )
            print(f"  ALNS：{alns.evaluation.official_score}")

            row["三种解官方Checker通过"] = initial_ok and gurobi_ok and alns_ok
        except Exception as exc:  # 单个实例失败不能阻断其余34个实验。
            errors.append(f"{type(exc).__name__}: {exc}")
            print(f"  失败：{errors[-1]}")

        row["错误信息"] = " | ".join(errors)
        rows.append(row)
        _save_csv(csv_path, rows)

    elapsed = perf_counter() - total_started
    print(f"\n全部完成，共用时 {elapsed:.1f} 秒。")
    print(f"CSV结果：{csv_path}")


def _record_result(row: dict[str, object], prefix: str, result: object) -> None:
    evaluation = result.evaluation
    metadata = result.solution.metadata
    row[f"{prefix}总分"] = evaluation.official_score
    separator = " " if prefix == "ALNS" else ""
    row[f"{prefix}{separator}HPRC"] = evaluation.hprc_violations
    row[f"{prefix}{separator}LPRC"] = evaluation.lprc_violations
    row[f"{prefix}{separator}Paint"] = evaluation.paint_changes
    if prefix == "Gurobi":
        row["Gurobi运行秒数"] = round(result.solution.runtime_sec, 4)
        row["Gurobi Best Bound"] = metadata.get("best_bound")
        row["Gurobi MIP Gap"] = metadata.get("mip_gap")
    elif prefix == "ALNS":
        row["ALNS运行秒数"] = round(result.solution.runtime_sec, 4)
        row["ALNS迭代数"] = metadata.get("iterations")


def _write_and_check(
    writer: RoadefSolutionWriter,
    checker: WindowsOfficialChecker,
    instance_dir: Path,
    solution_dir: Path,
    result: object,
    label: str,
) -> bool:
    path = solution_dir / f"{instance_dir.name}_{label}.txt"
    writer.write(result.solution, path)
    report = checker.check(instance_dir, path)
    evaluation = result.evaluation
    return (
        report.is_valid
        and report.score == evaluation.official_score
        and report.hprc_violations == evaluation.hprc_violations
        and report.lprc_violations == evaluation.lprc_violations
        and report.paint_changes == evaluation.paint_changes
    )


def _save_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _load_existing_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


if __name__ == "__main__":
    main()
