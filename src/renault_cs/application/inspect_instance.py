"""实例检视用例：产生可序列化的规模、约束和目标摘要。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from renault_cs.application.ports import InstanceParser


@dataclass(frozen=True, slots=True)
class InstanceSummary:
    """命令行与报告共享的实例摘要。"""

    name: str
    previous_day_vehicle_count: int
    planning_vehicle_count: int
    color_count: int
    hprc_count: int
    lprc_count: int
    paint_batch_limit: int
    objective_order: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def inspect_instance(instance_dir: Path, parser: InstanceParser) -> InstanceSummary:
    """解析实例并返回不含大型车辆明细的摘要。"""

    instance = parser.parse(instance_dir)
    return InstanceSummary(
        name=instance.name,
        previous_day_vehicle_count=len(instance.previous_day_vehicles),
        planning_vehicle_count=len(instance.planning_day_vehicles),
        color_count=instance.color_count,
        hprc_count=instance.hprc_count,
        lprc_count=instance.lprc_count,
        paint_batch_limit=instance.paint_batch_limit,
        objective_order=tuple(item.kind.value for item in instance.objectives),
    )
