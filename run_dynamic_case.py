"""直接点击 Run：读取动态事件存档并执行滚动重排。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path


# 只需修改这里即可切换案例与输出目录。
PROJECT_ROOT = Path(__file__).resolve().parent
CASE_FILE = PROJECT_ROOT / "cases" / "emergency_capacity_case_01.json"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "dynamic_rescheduling"

SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from renault_cs.dynamic_rescheduling import (
    RollingAlnsRescheduler,
    extend_instance,
    load_dynamic_scenario,
)
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
    for change in scenario.capacity_changes:
        print(
            f"临时降产：{change.constraint_id}，位置{change.start_position}～"
            f"{change.end_position}，调整为{change.numerator}/{change.denominator}"
        )

    result = RollingAlnsRescheduler(RenaultEvaluator()).solve(instance, scenario)
    original_sequence = tuple(
        vehicle.ident
        for vehicle in sorted(
            instance.planning_day_vehicles,
            key=lambda vehicle: (vehicle.original_rank, vehicle.ident),
        )
    )
    frozen_prefix_ok = (
        result.solution.vehicle_ids[: result.fixed_prefix_length]
        == original_sequence[: result.fixed_prefix_length]
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    solution_path = OUTPUT_DIR / f"{scenario.name}_solution.txt"
    report_path = OUTPUT_DIR / f"{scenario.name}_report.json"
    RoadefSolutionWriter().write(result.solution, solution_path)

    report = {
        "scenario": scenario.name,
        "source_instance": instance.name,
        "extended_instance": extended.name,
        "original_vehicle_count": len(instance.planning_day_vehicles),
        "emergency_order_count": len(scenario.emergency_orders),
        "final_vehicle_count": len(result.solution.vehicle_ids),
        "fixed_prefix_length": result.fixed_prefix_length,
        "frozen_prefix_valid": frozen_prefix_ok,
        "iterations": result.iterations,
        "accepted_iterations": result.accepted_iterations,
        "operator_weights": result.operator_weights,
        "before": asdict(result.before),
        "after": asdict(result.after),
        "solution_file": str(solution_path),
        "official_checker_applicable": False,
        "official_checker_note": "新增车辆与动态N/P约束不属于原始赛题，使用扩展Evaluator验证。"
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n重排前 → 重排后")
    print(
        f"临时产能违法：{result.before.temporary_capacity_violations} → "
        f"{result.after.temporary_capacity_violations}"
    )
    print(f"官方口径分：{result.before.official_score} → {result.after.official_score}")
    print(f"移动原订单：{result.before.moved_vehicle_count} → {result.after.moved_vehicle_count}")
    print(f"平均位置偏移：{result.after.average_position_shift:.2f}")
    print(f"最大位置偏移：{result.after.maximum_position_shift}")
    print(f"冻结前缀保持不变：{frozen_prefix_ok}")
    print(f"迭代数：{result.iterations}，接受：{result.accepted_iterations}")
    print(f"解文件：{solution_path}")
    print(f"报告：{report_path}")


if __name__ == "__main__":
    main()
