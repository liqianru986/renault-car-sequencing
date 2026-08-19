"""直接点击 Run：在两个代表实例上比较 ALNS 时间与迭代预算。"""

from __future__ import annotations

import csv
import sys
from pathlib import Path


# ======================== 调优时只修改这里 ========================
PROJECT_ROOT = Path(__file__).resolve().parent
EXPERIMENTS = (
    (
        "medium_335",
        PROJECT_ROOT.parent / "Instances_set_A" / "Instances" / "064_38_2_EP_RAF_ENP_ch2",
    ),
    (
        "large_1260",
        PROJECT_ROOT.parent / "Instances_set_A" / "Instances" / "024_38_3_EP_ENP_RAF",
    ),
)
TIME_LIMITS_SEC = (10.0, 30.0)
MAX_ITERATIONS = (25, 50, 100)
SEEDS = (42,)

DESTROY_FRACTION = 0.12
MAX_DESTROY_COUNT = 8
CANDIDATE_LIMIT = 30
REGRET_SAMPLE_SIZE = 6
LOCAL_SEARCH_TRIALS = 20
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
from renault_cs.infrastructure.roadef_parser import RoadefParser


def main() -> None:
    """运行小规模参数矩阵并写出便于比较的 CSV。"""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    output_dir = PROJECT_ROOT / "outputs" / "alns_tuning"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "tuning_results.csv"

    parser = RoadefParser()
    evaluator = RenaultEvaluator()
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
                                "segment_length": SEGMENT_LENGTH,
                                "reaction": REACTION,
                                "cooling": COOLING,
                            },
                        ),
                    )
                    metadata = result.solution.metadata
                    evaluation = result.evaluation
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
                    }
                    rows.append(row)
                    _save_csv(output_path, rows)
                    print(
                        f"  {time_limit:>4.0f}s / {iteration_limit:>3} iter | "
                        f"实际 {metadata['iterations']:>3} iter | "
                        f"{metadata['initial_score']} -> {evaluation.official_score}"
                    )

    print(f"\n结果已保存：{output_path}")


def _save_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
