"""直接点击 Run：读取动态事件存档并执行滚动重排。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path


# 只需修改这里即可切换案例与输出目录。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASE_FILE = PROJECT_ROOT / "cases" / "set_a_emergency_capacity_case_01.json"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "dynamic_rescheduling"

SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from renault_cs.dynamic_rescheduling import (
    DynamicGurobiRescheduler,
    RollingAlnsRescheduler,
    extend_instance,
    load_dynamic_scenario,
)
from renault_cs.algorithms.alns import AlnsSolver
from renault_cs.application.config import SolveConfig
from renault_cs.evaluation.evaluator import RenaultEvaluator
from renault_cs.infrastructure.roadef_parser import RoadefParser
from renault_cs.infrastructure.solution_io import RoadefSolutionWriter


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    scenario = load_dynamic_scenario(CASE_FILE)
    instance = RoadefParser().parse(scenario.instance_path)
    extended = extend_instance(instance, scenario)

    print("========== Renault 动态滚动重排 ==========")
    print(f"案例：{scenario.name}")
    print(f"原始实例：{instance.name}")
    print(f"原始车辆：{len(instance.planning_day_vehicles)}")
    print(f"紧急订单：{len(scenario.emergency_orders)}")
    print(f"已执行位置：{scenario.current_position}")
    print(f"冻结窗口：{scenario.freeze_length}")
    print(
        f"扰动预算：最多移动{scenario.max_moved_vehicles}辆，"
        f"总偏移不超过{scenario.max_total_position_shift}"
    )
    for change in scenario.capacity_changes:
        print(
            f"临时降产：{change.constraint_id}，位置{change.start_position}～"
            f"{change.end_position}，调整为{change.numerator}/{change.denominator}"
        )

    evaluator = RenaultEvaluator()
    print("\n先求解无扰动基准计划……")
    baseline = AlnsSolver(evaluator).solve(
        instance,
        SolveConfig(
            time_limit_sec=scenario.time_limit_sec,
            seed=scenario.seed,
            algorithm_parameters={"vfls_trials": 100, "vfls_interval": 10},
        ),
    )
    original_sequence = baseline.solution.vehicle_ids
    print(f"基准计划得分：{baseline.evaluation.official_score}")

    print("执行动态 ALNS……")
    alns_result = RollingAlnsRescheduler(evaluator).solve(
        instance, scenario, original_sequence
    )
    print("执行动态 Gurobi……")
    gurobi_result = DynamicGurobiRescheduler().solve(
        instance, scenario, original_sequence
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    alns_solution_path = OUTPUT_DIR / f"{scenario.name}_alns_solution.txt"
    gurobi_solution_path = OUTPUT_DIR / f"{scenario.name}_gurobi_solution.txt"
    report_path = OUTPUT_DIR / f"{scenario.name}_report.json"
    writer = RoadefSolutionWriter()
    writer.write(alns_result.solution, alns_solution_path)
    writer.write(gurobi_result.solution, gurobi_solution_path)

    def result_payload(result: object) -> dict[str, object]:
        frozen_ok = (
            result.solution.vehicle_ids[: result.fixed_prefix_length]
            == original_sequence[: result.fixed_prefix_length]
        )
        emergency_positions = {
            ident: result.solution.vehicle_ids.index(ident)
            for ident in (vehicle.ident for vehicle in scenario.emergency_orders)
        }
        return {
            "algorithm": result.solution.algorithm,
            "runtime_sec": result.solution.runtime_sec,
            "frozen_prefix_valid": frozen_ok,
            "emergency_order_positions": emergency_positions,
            "before": asdict(result.before),
            "after": asdict(result.after),
            "metadata": dict(result.solution.metadata),
        }

    report = {
        "scenario": scenario.name,
        "source_instance": instance.name,
        "extended_instance": extended.name,
        "original_vehicle_count": len(instance.planning_day_vehicles),
        "emergency_order_count": len(scenario.emergency_orders),
        "final_vehicle_count": len(extended.planning_day_vehicles),
        "baseline_static": {
            "official_score": baseline.evaluation.official_score,
            "objective_vector": baseline.evaluation.objective_vector,
            "runtime_sec": baseline.solution.runtime_sec,
        },
        "objective_weights": {
            "official_score_weight": scenario.official_score_weight,
            "moved_vehicle_weight": scenario.moved_vehicle_weight,
            "position_shift_weight": scenario.position_shift_weight,
        },
        "disruption_budget": {
            "max_moved_vehicles": scenario.max_moved_vehicles,
            "max_total_position_shift": scenario.max_total_position_shift,
        },
        "results": {
            "dynamic_alns": result_payload(alns_result),
            "dynamic_gurobi": result_payload(gurobi_result),
        },
        "solution_files": {
            "dynamic_alns": str(alns_solution_path),
            "dynamic_gurobi": str(gurobi_solution_path),
        },
        "official_checker_applicable": False,
        "official_checker_note": "新增车辆与动态N/P约束不属于原始赛题，使用扩展Evaluator验证。"
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n重排结果")
    for label, result in (("动态ALNS", alns_result), ("动态Gurobi", gurobi_result)):
        print(
            f"{label}：临时违法 {result.before.temporary_capacity_violations} → "
            f"{result.after.temporary_capacity_violations}，官方分 "
            f"{result.before.official_score} → {result.after.official_score}，"
            f"移动 {result.after.moved_vehicle_count} 辆，总偏移 "
            f"{result.after.total_position_shift}，平均偏移 "
            f"{result.after.average_position_shift:.2f}"
        )
    print(f"报告：{report_path}")


if __name__ == "__main__":
    main()
