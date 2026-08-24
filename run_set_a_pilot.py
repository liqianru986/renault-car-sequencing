"""直接点击Run：在三个Set A代表实例上验证SeqRank、Gurobi与ALNS。"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


# ======================== 首轮Pilot参数 ========================
PROJECT_ROOT = Path(__file__).resolve().parent
INSTANCE_NAMES = (
    "064_38_2_EP_RAF_ENP_ch2",  # 335辆
    "022_3_4_EP_RAF_ENP",       # 485辆
    "024_38_3_EP_ENP_RAF",      # 1260辆
)
TIME_LIMIT_SEC = 10.0
SEED = 42

MAX_ITERATIONS = 100
MAX_DESTROY_COUNT = 8
CANDIDATE_LIMIT = 30
REGRET_SAMPLE_SIZE = 6
LOCAL_SEARCH_TRIALS = 20
# =============================================================


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


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    dataset_dir = PROJECT_ROOT.parent / "Instances_set_A" / "Instances"
    checker = WindowsOfficialChecker(
        PROJECT_ROOT.parent / "checkers" / "WINDOWS" / "exeCarSeq.exe"
    )
    output_dir = PROJECT_ROOT / "outputs" / "set_a_pilot"
    solution_dir = output_dir / "solutions"
    solution_dir.mkdir(parents=True, exist_ok=True)
    output_csv = output_dir / "set_a_pilot_results.csv"

    parser = RoadefParser()
    evaluator = RenaultEvaluator()
    writer = RoadefSolutionWriter()
    rows: list[dict[str, object]] = []

    for instance_name in INSTANCE_NAMES:
        instance_path = dataset_dir / instance_name
        instance = parser.parse(instance_path)
        print(f"\n{instance.name} | {len(instance.planning_day_vehicles)}辆")

        solvers = (
            ("seqrank", SeqRankSolver(evaluator), SolveConfig(time_limit_sec=1.0)),
            (
                "gurobi",
                GurobiExactSolver(evaluator),
                SolveConfig(
                    time_limit_sec=TIME_LIMIT_SEC,
                    seed=SEED,
                    algorithm_parameters={
                        "mip_gap": 0.01,
                        "threads": 0,
                        "log_to_console": False,
                        "use_mip_start": True,
                    },
                ),
            ),
            (
                "alns",
                AlnsSolver(evaluator),
                SolveConfig(
                    time_limit_sec=TIME_LIMIT_SEC,
                    max_iterations=MAX_ITERATIONS,
                    seed=SEED,
                    algorithm_parameters={
                        "max_destroy_count": MAX_DESTROY_COUNT,
                        "candidate_limit": CANDIDATE_LIMIT,
                        "regret_sample_size": REGRET_SAMPLE_SIZE,
                        "local_search_trials": LOCAL_SEARCH_TRIALS,
                        "segment_length": 20,
                    },
                ),
            ),
        )

        initial_score = None
        for label, solver, config in solvers:
            result = solver.solve(instance, config)
            evaluation = result.evaluation
            solution_path = solution_dir / f"{instance.name}_{label}.txt"
            writer.write(result.solution, solution_path)
            report = checker.check(instance_path, solution_path)
            checker_passed = report.is_valid and report.score == evaluation.official_score
            if label == "seqrank":
                initial_score = evaluation.official_score

            metadata = result.solution.metadata
            improvement = (
                100.0 * (initial_score - evaluation.official_score) / initial_score
                if initial_score
                else 0.0
            )
            rows.append(
                {
                    "instance": instance.name,
                    "vehicles": len(instance.planning_day_vehicles),
                    "algorithm": label,
                    "time_limit_sec": config.time_limit_sec,
                    "runtime_sec": round(result.solution.runtime_sec, 4),
                    "objective_vector": json.dumps(evaluation.objective_vector),
                    "official_score": evaluation.official_score,
                    "relative_improvement_pct": round(improvement, 4),
                    "checker_passed": checker_passed,
                    "iterations": metadata.get("iterations"),
                    "accepted": metadata.get("accepted"),
                    "improvements": metadata.get("improvements"),
                    "best_found_sec": metadata.get("best_found_sec"),
                    "local_search_improvements": metadata.get("local_search_improvements"),
                    "destroy_operators": json.dumps(
                        metadata.get("destroy_operators", {}), ensure_ascii=False
                    ),
                    "repair_operators": json.dumps(
                        metadata.get("repair_operators", {}), ensure_ascii=False
                    ),
                }
            )
            print(
                f"  {label:<7} score={evaluation.official_score:<10} "
                f"vector={evaluation.objective_vector} checker={checker_passed}"
            )
            _save_csv(output_csv, rows)

    print(f"\nPilot结果：{output_csv}")


def _save_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
