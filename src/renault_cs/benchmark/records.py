"""Benchmark 记录模型：定义单次运行、收敛轨迹和实例汇总数据结构。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConvergencePoint:
    """算法 anytime 轨迹中的一个时间点。"""

    elapsed_sec: float
    iteration: int
    objective_vector: tuple[int, ...]
    official_score: int | None


@dataclass(frozen=True, slots=True)
class BenchmarkRecord:
    """一个 instance、algorithm 和 seed 的原子实验记录。"""

    dataset: str
    instance_name: str
    scenario_class: str
    vehicle_count: int
    color_count: int
    hprc_count: int
    lprc_count: int
    algorithm: str
    seed: int | None
    time_limit_sec: float
    runtime_sec: float
    feasible: bool
    checker_passed: bool | None
    paint_changes: int
    hprc_violations: int
    lprc_violations: int
    objective_vector: tuple[int, ...]
    official_score: int | None
    best_bound: float | None
    mip_gap: float | None
    node_count: float | None
    best_found_time_sec: float | None
