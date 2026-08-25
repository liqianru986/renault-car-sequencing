"""直接点击 Run：在两个代表实例上比较 ALNS 时间与迭代预算。"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


# ======================== 调优时只修改这里 ========================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = (
    (
        "medium_485",
        PROJECT_ROOT.parent / "Instances_set_A" / "Instances" / "022_3_4_EP_RAF_ENP",
    ),
)
TIME_LIMITS_SEC = (60.0,)
MAX_ITERATIONS = (10_000,)
SEEDS = (41, 42, 43)

DESTROY_FRACTION = 0.12
MAX_DESTROY_COUNT = 8
CANDIDATE_LIMIT = 30
REGRET_SAMPLE_SIZE = 6
LOCAL_SEARCH_TRIALS = 20
PAINT_SEARCH_TRIALS = 20
CHAIN_SEARCH_TRIALS = 0
BLOCK_SEARCH_TRIALS = 0
STRUCTURED_SEARCH_INTERVAL = 50
VFLS_TRIALS = 100
VFLS_INTERVAL = 10
SEGMENT_LENGTH = 20
REACTION = 0.20
COOLING = 0.995
# ================================================================


SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from renault_cs.algorithms.alns import AlnsSolver
from renault_cs.application.config import SolveConfig
from renault_cs.evaluation.evaluator import RenaultEvaluator
from renault_cs.infrastructure.checker_adapter import WindowsOfficialChecker
from renault_cs.infrastructure.roadef_parser import RoadefParser
from renault_cs.infrastructure.solution_io import RoadefSolutionWriter


def main() -> None:
    """运行小规模参数矩阵并写出便于比较的 CSV。"""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    output_dir = PROJECT_ROOT / "outputs" / "alns_tuning"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"tuning_results_{int(TIME_LIMITS_SEC[0])}s.csv"
    solution_dir = output_dir / f"solutions_{int(TIME_LIMITS_SEC[0])}s"
    solution_dir.mkdir(exist_ok=True)

    parser = RoadefParser()
    evaluator = RenaultEvaluator()
    writer = RoadefSolutionWriter()
    checker = WindowsOfficialChecker(
        PROJECT_ROOT.parent / "checkers" / "WINDOWS" / "exeCarSeq.exe"
    )
    rows: list[dict[str, object]] = []

    for label, instance_path in EXPERIMENTS:
        instance = parser.parse(instance_path)
        vehicle_count = len(instance.planning_day_vehicles)
        print(f"\n{label}: {instance.name}, {vehicle_count} 辆")

        for time_limit in TIME_LIMITS_SEC:
            for iteration_limit in MAX_ITERATIONS:
                for seed in SEEDS:
                    result = AlnsSolver(evaluator).solve(
                        instance,
                        SolveConfig(
                            time_limit_sec=time_limit,
                            max_iterations=iteration_limit,
                            seed=seed,
                            algorithm_parameters={
                                "destroy_fraction": DESTROY_FRACTION,
                                "max_destroy_count": MAX_DESTROY_COUNT,
                                "candidate_limit": CANDIDATE_LIMIT,
                                "regret_sample_size": REGRET_SAMPLE_SIZE,
                                "local_search_trials": LOCAL_SEARCH_TRIALS,
                                "paint_search_trials": PAINT_SEARCH_TRIALS,
                                "chain_search_trials": CHAIN_SEARCH_TRIALS,
                                "block_search_trials": BLOCK_SEARCH_TRIALS,
                                "structured_search_interval": STRUCTURED_SEARCH_INTERVAL,
                                "vfls_trials": VFLS_TRIALS,
                                "vfls_interval": VFLS_INTERVAL,
                                "segment_length": SEGMENT_LENGTH,
                                "reaction": REACTION,
                                "cooling": COOLING,
                            },
                        ),
                    )
                    metadata = result.solution.metadata
                    evaluation = result.evaluation
                    solution_path = solution_dir / f"{instance.name}_seed{seed}.txt"
                    writer.write(result.solution, solution_path)
                    trace_path = solution_dir / f"{instance.name}_seed{seed}_trace.json"
                    trace_path.write_text(
                        json.dumps(
                            metadata["convergence_trace"],
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    checker_report = checker.check(instance_path, solution_path)
                    row = {
                        "case": label,
                        "instance": instance.name,
                        "vehicles": vehicle_count,
                        "time_limit_sec": time_limit,
                        "max_iterations": iteration_limit,
                        "seed": seed,
                        "runtime_sec": round(result.solution.runtime_sec, 4),
                        "iterations": metadata["iterations"],
                        "accepted": metadata["accepted"],
                        "improvements": metadata["improvements"],
                        "best_found_sec": round(float(metadata["best_found_sec"]), 4),
                        "initial_score": metadata["initial_score"],
                        "best_score": evaluation.official_score,
                        "hprc": evaluation.hprc_violations,
                        "lprc": evaluation.lprc_violations,
                        "paint": evaluation.paint_changes,
                        "checker_passed": (
                            checker_report.is_valid
                            and checker_report.score == evaluation.official_score
                        ),
                        "structured_improvements": metadata["chain_search_improvements"],
                    }
                    rows.append(row)
                    _save_csv(output_path, rows)
                    print(
                        f"  {time_limit:>4.0f}s / {iteration_limit:>3} iter | "
                        f"实际 {metadata['iterations']:>3} iter | "
                        f"{metadata['initial_score']} -> {evaluation.official_score} | "
                        f"checker={row['checker_passed']}"
                    )

    print(f"\n结果已保存：{output_path}")


def _save_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
