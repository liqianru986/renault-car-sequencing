"""问题数据模型：定义车辆、N/P 约束、目标规则和不可变的完整 instance。"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from renault_cs.domain.enums import HprcDifficulty, ObjectiveKind, RatioPriority
from renault_cs.domain.exceptions import DomainValidationError


@dataclass(frozen=True, slots=True)
class RatioConstraint:
    """一条 N/P 滑动窗口比例约束。"""

    ident: str
    numerator: int
    denominator: int
    priority: RatioPriority

    def __post_init__(self) -> None:
        if not self.ident:
            raise DomainValidationError("RatioConstraint.ident cannot be empty")
        if self.denominator <= 0:
            raise DomainValidationError("Ratio denominator must be positive")
        if self.numerator < 0 or self.numerator > self.denominator:
            raise DomainValidationError("Ratio numerator must satisfy 0 <= N <= P")

    @property
    def ratio(self) -> float:
        """返回 N/P 的数值利用率，仅用于统计和诊断。"""

        return self.numerator / self.denominator

    @property
    def is_high_priority(self) -> bool:
        """该约束是否属于 HPRC。"""

        return self.priority is RatioPriority.HIGH


@dataclass(frozen=True, slots=True)
class Vehicle:
    """一辆官方订单车辆；option_flags 与 instance 的约束顺序严格对齐。"""

    ident: str
    production_date: str
    original_rank: int
    paint_color: str
    option_flags: tuple[bool, ...]

    def __post_init__(self) -> None:
        if not self.ident:
            raise DomainValidationError("Vehicle.ident cannot be empty")
        if not self.production_date:
            raise DomainValidationError("Vehicle.production_date cannot be empty")
        if self.original_rank <= 0:
            raise DomainValidationError("Vehicle.original_rank must be positive")
        if not self.paint_color:
            raise DomainValidationError("Vehicle.paint_color cannot be empty")


@dataclass(frozen=True, slots=True)
class ObjectiveSpec:
    """一个官方目标及其 rank；raw_name 用于与原文件和 Checker 追溯对齐。"""

    rank: int
    kind: ObjectiveKind
    raw_name: str
    hprc_difficulty: HprcDifficulty | None = None

    def __post_init__(self) -> None:
        if self.rank <= 0:
            raise DomainValidationError("Objective rank must be positive")
        if not self.raw_name:
            raise DomainValidationError("Objective raw_name cannot be empty")
        if self.kind is not ObjectiveKind.HPRC_VIOLATIONS and self.hprc_difficulty is not None:
            raise DomainValidationError("HPRC difficulty may only be attached to the HPRC objective")


@dataclass(frozen=True, slots=True)
class ProblemInstance:
    """一个完整赛题场景，包含固定的 D-1 上下文和待排序的 D 日车辆。"""

    name: str
    paint_batch_limit: int
    ratio_constraints: tuple[RatioConstraint, ...]
    objectives: tuple[ObjectiveSpec, ...]
    previous_day_vehicles: tuple[Vehicle, ...]
    planning_day_vehicles: tuple[Vehicle, ...]
    _vehicle_by_id: Mapping[str, Vehicle] = field(init=False, repr=False, compare=False)
    _constraint_index: Mapping[str, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.name:
            raise DomainValidationError("ProblemInstance.name cannot be empty")
        if self.paint_batch_limit <= 0:
            raise DomainValidationError("paint_batch_limit must be positive")
        if not self.planning_day_vehicles:
            raise DomainValidationError("planning_day_vehicles cannot be empty")

        constraint_ids = [item.ident for item in self.ratio_constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise DomainValidationError("Ratio constraint identifiers must be unique")

        ranks = [item.rank for item in self.objectives]
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise DomainValidationError("Objective ranks must be contiguous and start at 1")

        all_vehicles = self.previous_day_vehicles + self.planning_day_vehicles
        vehicle_ids = [item.ident for item in all_vehicles]
        if len(vehicle_ids) != len(set(vehicle_ids)):
            raise DomainValidationError("Vehicle identifiers must be unique across D-1 and D")
        for vehicle in all_vehicles:
            if len(vehicle.option_flags) != len(self.ratio_constraints):
                raise DomainValidationError(
                    f"Vehicle {vehicle.ident} has {len(vehicle.option_flags)} option flags; "
                    f"expected {len(self.ratio_constraints)}"
                )

        object.__setattr__(
            self,
            "_vehicle_by_id",
            MappingProxyType({item.ident: item for item in all_vehicles}),
        )
        object.__setattr__(
            self,
            "_constraint_index",
            MappingProxyType({item.ident: index for index, item in enumerate(self.ratio_constraints)}),
        )

    @property
    def vehicle_by_id(self) -> Mapping[str, Vehicle]:
        """所有 D-1/D 车辆的只读索引。"""

        return self._vehicle_by_id

    @property
    def constraint_index(self) -> Mapping[str, int]:
        """约束 Ident 到 option_flags 位置的只读映射。"""

        return self._constraint_index

    @property
    def planning_vehicle_ids(self) -> frozenset[str]:
        """待排序 D 日车辆的标识符集合。"""

        return frozenset(item.ident for item in self.planning_day_vehicles)

    @property
    def hprc_count(self) -> int:
        """HPRC 数量。"""

        return sum(item.is_high_priority for item in self.ratio_constraints)

    @property
    def lprc_count(self) -> int:
        """LPRC 数量。"""

        return len(self.ratio_constraints) - self.hprc_count

    @property
    def color_count(self) -> int:
        """D 日待排车辆的颜色种类数。"""

        return len({item.paint_color for item in self.planning_day_vehicles})

    def has_option(self, vehicle: Vehicle, constraint_ident: str) -> bool:
        """以约束 Ident 查询车辆是否具有对应装配特征。"""

        return vehicle.option_flags[self.constraint_index[constraint_ident]]

