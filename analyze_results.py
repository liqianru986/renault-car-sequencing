"""汇总静态35实例与动态案例实验，生成可复现的Markdown分析。"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATIC_CSV = ROOT / "outputs" / "all_instances_comparison_60s_final_v3" / "all_instances_results.csv"
DYNAMIC_JSON = (
    ROOT
    / "outputs"
    / "dynamic_rescheduling"
    / "set_a_emergency_capacity_case_01_report.json"
)
OUTPUT = ROOT / "outputs" / "experiment_summary_60s.md"


def main() -> None:
    rows = _read_static_rows(STATIC_CSV)
    dynamic = json.loads(DYNAMIC_JSON.read_text(encoding="utf-8"))
    completed = [row for row in rows if row.get("ALNS总分") and row.get("Gurobi总分")]
    alns_wins = sum(_number(row["ALNS总分"]) < _number(row["Gurobi总分"]) for row in completed)
    gurobi_wins = sum(_number(row["Gurobi总分"]) < _number(row["ALNS总分"]) for row in completed)
    ties = len(completed) - alns_wins - gurobi_wins

    lines = [
        "# Renault Car Sequencing 60秒实验汇总",
        "",
        f"- 已完成实例：{len(completed)}/{len(rows)}",
        f"- ALNS优于Gurobi：{alns_wins}",
        f"- Gurobi优于ALNS：{gurobi_wins}",
        f"- 同分：{ties}",
        f"- Checker全部通过：{all(row.get('三种解官方Checker通过') == 'True' for row in completed)}",
        "",
        "## 分数据集统计",
        "",
        "| 数据集 | 实例数 | ALNS胜 | Gurobi胜 | 同分 | ALNS相对SeqRank中位改善 | Gurobi相对SeqRank中位改善 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for dataset in sorted({row["数据集"] for row in completed}):
        group = [row for row in completed if row["数据集"] == dataset]
        aw = sum(_number(row["ALNS总分"]) < _number(row["Gurobi总分"]) for row in group)
        gw = sum(_number(row["Gurobi总分"]) < _number(row["ALNS总分"]) for row in group)
        improvements_a = [_improvement(row, "ALNS总分") for row in group]
        improvements_g = [_improvement(row, "Gurobi总分") for row in group]
        lines.append(
            f"| {dataset} | {len(group)} | {aw} | {gw} | {len(group)-aw-gw} | "
            f"{statistics.median(improvements_a):.2f}% | "
            f"{statistics.median(improvements_g):.2f}% |"
        )

    lines.extend(["", "## 动态案例", ""])
    before = dynamic["results"]["dynamic_alns"]["before"]
    for key, label in (("dynamic_alns", "动态ALNS"), ("dynamic_gurobi", "动态Gurobi")):
        item = dynamic["results"][key]
        after = item["after"]
        lines.extend(
            [
                f"### {label}",
                "",
                f"- 临时产能违法：{before['temporary_capacity_violations']} → "
                f"{after['temporary_capacity_violations']}",
                f"- 官方口径分：{before['official_score']} → {after['official_score']}",
                f"- 移动车辆数：{after['moved_vehicle_count']}",
                f"- 总/平均/最大位置偏移：{after['total_position_shift']} / "
                f"{after['average_position_shift']:.2f} / {after['maximum_position_shift']}",
                f"- 冻结前缀有效：{item['frozen_prefix_valid']}",
                "",
            ]
        )

    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"汇总已生成：{OUTPUT}")


def _read_static_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def _number(value: str) -> float:
    return float(value)


def _improvement(row: dict[str, str], result_column: str) -> float:
    initial = _number(row["官方初始总分"])
    return 100.0 * (initial - _number(row[result_column])) / initial if initial else 0.0


if __name__ == "__main__":
    main()
