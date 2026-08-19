"""Benchmark 报告：汇总运行完整性；目标改善保留到同实例配对比较。"""

from __future__ import annotations

from collections import defaultdict
from statistics import median

from renault_cs.benchmark.records import BenchmarkRecord


def summarize_records(records: list[BenchmarkRecord]) -> list[dict[str, object]]:
    """生成适合 JSON/表格展示的算法级摘要。"""

    grouped: dict[str, list[BenchmarkRecord]] = defaultdict(list)
    for record in records:
        grouped[record.algorithm].append(record)

    summaries: list[dict[str, object]] = []
    for algorithm, group in sorted(grouped.items()):
        checked = [item for item in group if item.checker_passed is not None]
        summaries.append(
            {
                "algorithm": algorithm,
                "runs": len(group),
                "instances": len({item.instance_name for item in group}),
                "feasible_rate": sum(item.feasible for item in group) / len(group),
                "checker_pass_rate": (
                    sum(bool(item.checker_passed) for item in checked) / len(checked)
                    if checked
                    else None
                ),
                "median_runtime_sec": median(item.runtime_sec for item in group),
            }
        )
    return summaries
