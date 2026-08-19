"""MILP 数据预处理：构造车辆类型、颜色索引和全部比例窗口常数。"""

from __future__ import annotations

from dataclasses import dataclass

from renault_cs.domain.enums import ObjectiveKind, RatioPriority
from renault_cs.domain.models import ProblemInstance
from renault_cs.exact.vehicle_types import VehicleType, aggregate_vehicle_types


@dataclass(frozen=True, slots=True)
class RatioWindow:
    """一条比例约束在一个窗口起点上的线性化数据。"""

    constraint_index: int
    constraint_id: str
    priority: RatioPriority
    start_position: int
    planning_positions: tuple[int, ...]
    previous_day_count: int
    allowed_count: int


@dataclass(frozen=True, slots=True)
class ExactModelData:
    """与 Gurobi API 无关、可独立测试的完整模型输入。"""

    instance_name: str
    position_count: int
    vehicle_types: tuple[VehicleType, ...]
    colors: tuple[str, ...]
    type_indices_by_color: dict[str, tuple[int, ...]]
    ratio_windows: tuple[RatioWindow, ...]
    previous_day_last_color: str | None
    paint_batch_limit: int
    objective_order: tuple[ObjectiveKind, ...]


def build_exact_model_data(instance: ProblemInstance) -> ExactModelData:
    """将领域 instance 转换为求解器无关的 MILP 数据。"""

    vehicle_types = aggregate_vehicle_types(instance)
    colors = tuple(sorted({item.paint_color for item in vehicle_types}))
    type_indices_by_color = {
        color: tuple(item.index for item in vehicle_types if item.paint_color == color)
        for color in colors
    }
    windows = _build_ratio_windows(instance)
    previous_color = (
        instance.previous_day_vehicles[-1].paint_color
        if instance.previous_day_vehicles
        else None
    )
    return ExactModelData(
        instance_name=instance.name,
        position_count=len(instance.planning_day_vehicles),
        vehicle_types=vehicle_types,
        colors=colors,
        type_indices_by_color=type_indices_by_color,
        ratio_windows=windows,
        previous_day_last_color=previous_color,
        paint_batch_limit=instance.paint_batch_limit,
        objective_order=tuple(item.kind for item in instance.objectives),
    )


def _build_ratio_windows(instance: ProblemInstance) -> tuple[RatioWindow, ...]:
    previous = instance.previous_day_vehicles
    planning_count = len(instance.planning_day_vehicles)
    previous_count = len(previous)
    windows: list[RatioWindow] = []

    for constraint_index, constraint in enumerate(instance.ratio_constraints):
        first_start = max(-previous_count, -(constraint.denominator - 1))
        for start in range(first_start, planning_count):
            nominal_end = start + constraint.denominator - 1
            planning_positions = tuple(
                range(max(0, start), min(nominal_end, planning_count - 1) + 1)
            )
            previous_matches = sum(
                vehicle.option_flags[constraint_index]
                for coordinate, vehicle in enumerate(previous, start=-previous_count)
                if start <= coordinate <= nominal_end
            )
            windows.append(
                RatioWindow(
                    constraint_index=constraint_index,
                    constraint_id=constraint.ident,
                    priority=constraint.priority,
                    start_position=start,
                    planning_positions=planning_positions,
                    previous_day_count=previous_matches,
                    allowed_count=constraint.numerator,
                )
            )
    return tuple(windows)
