"""车辆类型聚合与解还原：压缩同颜色、同选装件车辆。"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Sequence

from renault_cs.domain.exceptions import DomainValidationError
from renault_cs.domain.models import ProblemInstance, Vehicle


@dataclass(frozen=True, slots=True)
class VehicleType:
    """MILP 中可交换的一组车辆。"""

    index: int
    paint_color: str
    option_flags: tuple[bool, ...]
    vehicle_ids: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.vehicle_ids)


def aggregate_vehicle_types(instance: ProblemInstance) -> tuple[VehicleType, ...]:
    """按 ``(Paint Color, option_flags)`` 精确聚合 D 日车辆。"""

    groups: dict[tuple[str, tuple[bool, ...]], list[Vehicle]] = defaultdict(list)
    for vehicle in instance.planning_day_vehicles:
        groups[(vehicle.paint_color, vehicle.option_flags)].append(vehicle)

    ordered_keys = sorted(groups, key=lambda key: (key[0], key[1]))
    result: list[VehicleType] = []
    for index, (color, flags) in enumerate(ordered_keys):
        vehicles = sorted(groups[(color, flags)], key=lambda item: (item.original_rank, item.ident))
        result.append(
            VehicleType(
                index=index,
                paint_color=color,
                option_flags=flags,
                vehicle_ids=tuple(vehicle.ident for vehicle in vehicles),
            )
        )
    return tuple(result)


def vehicle_to_type_index(vehicle_types: Sequence[VehicleType]) -> dict[str, int]:
    """建立真实车辆 Ident 到聚合类型的只读语义映射。"""

    mapping: dict[str, int] = {}
    for vehicle_type in vehicle_types:
        for vehicle_id in vehicle_type.vehicle_ids:
            if vehicle_id in mapping:
                raise DomainValidationError(f"Vehicle appears in multiple types: {vehicle_id}")
            mapping[vehicle_id] = vehicle_type.index
    return mapping


def reconstruct_vehicle_ids(
    type_sequence: Sequence[int],
    vehicle_types: Sequence[VehicleType],
) -> tuple[str, ...]:
    """按类型内原 SeqRank 稳定还原真实车辆编号。"""

    queues = {item.index: deque(item.vehicle_ids) for item in vehicle_types}
    result: list[str] = []
    for position, type_index in enumerate(type_sequence):
        queue = queues.get(type_index)
        if queue is None:
            raise DomainValidationError(
                f"Unknown vehicle type {type_index} at sequence position {position}"
            )
        if not queue:
            raise DomainValidationError(
                f"Vehicle type {type_index} is used more often than its available count"
            )
        result.append(queue.popleft())

    unused = sum(len(queue) for queue in queues.values())
    if unused:
        raise DomainValidationError(f"Type sequence leaves {unused} vehicles unused")
    return tuple(result)
