"""批量实验用例：发现官方实例目录并调用统一 Benchmark Runner。"""

from __future__ import annotations

from pathlib import Path

from renault_cs.application.config import BenchmarkConfig
from renault_cs.application.ports import SequencingSolver
from renault_cs.benchmark.records import BenchmarkRecord
from renault_cs.benchmark.reporting import summarize_records
from renault_cs.benchmark.runner import BenchmarkRunner
from renault_cs.infrastructure.repositories import LocalResultRepository


def discover_instance_dirs(dataset_dir: Path) -> list[Path]:
    """查找直接包含四个官方文件的一级实例目录。"""

    dataset_dir = Path(dataset_dir)
    if not dataset_dir.is_dir():
        raise ValueError(f"Dataset directory does not exist: {dataset_dir}")
    required = {
        "vehicles.txt",
        "ratios.txt",
        "optimization_objectives.txt",
        "paint_batch_limit.txt",
    }
    instance_dirs = [
        path
        for path in dataset_dir.iterdir()
        if path.is_dir() and required.issubset({file.name for file in path.iterdir()})
    ]
    if not instance_dirs:
        raise ValueError(f"No official instance directories found in: {dataset_dir}")
    return sorted(instance_dirs, key=lambda path: path.name)


def run_benchmark(
    dataset_dir: Path,
    solvers: list[SequencingSolver],
    config: BenchmarkConfig,
    *,
    runner: BenchmarkRunner,
    repository: LocalResultRepository,
) -> list[BenchmarkRecord]:
    """运行数据集并同时保存原子记录与算法级摘要。"""

    dataset_path = Path(dataset_dir)
    dataset_name = (
        dataset_path.parent.name if dataset_path.name.lower() == "instances" else dataset_path.name
    )
    records = runner.run(
        discover_instance_dirs(dataset_dir),
        solvers,
        config,
        dataset=dataset_name,
    )
    repository.save_json("benchmark_summary", summarize_records(records))
    return records
