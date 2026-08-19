"""ROADEF 2005 实例解析器：将四个官方文本文件转换为领域对象。"""

from __future__ import annotations

import csv
from pathlib import Path

from renault_cs.domain.enums import HprcDifficulty, ObjectiveKind, RatioPriority
from renault_cs.domain.exceptions import DomainValidationError, InstanceFormatError
from renault_cs.domain.models import ObjectiveSpec, ProblemInstance, RatioConstraint, Vehicle


_OBJECTIVE_MAPPING = {
    "paint_color_batches": (ObjectiveKind.PAINT_COLOR_CHANGES, None),
    "high_priority_level_and_easy_to_satisfy_ratio_constraints": (
        ObjectiveKind.HPRC_VIOLATIONS,
        HprcDifficulty.EASY,
    ),
    "high_priority_level_and_difficult_to_satisfy_ratio_constraints": (
        ObjectiveKind.HPRC_VIOLATIONS,
        HprcDifficulty.DIFFICULT,
    ),
    "low_priority_level_ratio_constraints": (ObjectiveKind.LPRC_VIOLATIONS, None),
}


class RoadefParser:
    """读取一个官方 instance 目录，并创建不可变的 ``ProblemInstance``。"""

    RATIOS_FILE = "ratios.txt"
    VEHICLES_FILE = "vehicles.txt"
    OBJECTIVES_FILE = "optimization_objectives.txt"
    PAINT_LIMIT_FILE = "paint_batch_limit.txt"

    def parse(self, instance_dir: Path) -> ProblemInstance:
        """解析四个官方文件；格式错误统一转换为可定位的业务异常。"""

        instance_dir = Path(instance_dir)
        if not instance_dir.is_dir():
            raise InstanceFormatError(f"Instance directory does not exist: {instance_dir}")

        try:
            constraints = self._parse_ratios(instance_dir / self.RATIOS_FILE)
            objectives = self._parse_objectives(instance_dir / self.OBJECTIVES_FILE)
            paint_limit = self._parse_paint_limit(instance_dir / self.PAINT_LIMIT_FILE)
            previous_day, planning_day = self._parse_vehicles(
                instance_dir / self.VEHICLES_FILE, constraints
            )
            return ProblemInstance(
                name=instance_dir.name,
                paint_batch_limit=paint_limit,
                ratio_constraints=constraints,
                objectives=objectives,
                previous_day_vehicles=previous_day,
                planning_day_vehicles=planning_day,
            )
        except InstanceFormatError:
            raise
        except (DomainValidationError, ValueError) as exc:
            raise InstanceFormatError(f"Invalid instance {instance_dir.name}: {exc}") from exc

    def _parse_ratios(self, path: Path) -> tuple[RatioConstraint, ...]:
        header, rows = self._read_table(path)
        self._require_header(path, header, ("Ratio", "Prio", "Ident"))

        constraints: list[RatioConstraint] = []
        for line_number, row in rows:
            ratio_text, priority_text, ident = row
            try:
                numerator_text, denominator_text = ratio_text.split("/", maxsplit=1)
                constraints.append(
                    RatioConstraint(
                        ident=ident,
                        numerator=int(numerator_text),
                        denominator=int(denominator_text),
                        priority=RatioPriority(int(priority_text)),
                    )
                )
            except (TypeError, ValueError, DomainValidationError) as exc:
                raise self._row_error(path, line_number, str(exc)) from exc

        if not constraints:
            raise InstanceFormatError(f"{path.name} contains no ratio constraints")
        return tuple(constraints)

    def _parse_objectives(self, path: Path) -> tuple[ObjectiveSpec, ...]:
        header, rows = self._read_table(path)
        self._require_header(path, header, ("rank", "objective name"))

        objectives: list[ObjectiveSpec] = []
        for line_number, row in rows:
            rank_text, raw_name = row
            mapping = _OBJECTIVE_MAPPING.get(raw_name)
            if mapping is None:
                raise self._row_error(path, line_number, f"unknown objective: {raw_name!r}")
            kind, difficulty = mapping
            try:
                objectives.append(
                    ObjectiveSpec(
                        rank=int(rank_text),
                        kind=kind,
                        raw_name=raw_name,
                        hprc_difficulty=difficulty,
                    )
                )
            except (ValueError, DomainValidationError) as exc:
                raise self._row_error(path, line_number, str(exc)) from exc

        if not objectives:
            raise InstanceFormatError(f"{path.name} contains no objectives")
        return tuple(sorted(objectives, key=lambda item: item.rank))

    def _parse_paint_limit(self, path: Path) -> int:
        header, rows = self._read_table(path)
        self._require_header(path, header, ("limitation",))
        if len(rows) != 1:
            raise InstanceFormatError(f"{path.name} must contain exactly one limitation")
        line_number, row = rows[0]
        try:
            limit = int(row[0])
        except ValueError as exc:
            raise self._row_error(path, line_number, "limitation must be an integer") from exc
        if limit <= 0:
            raise self._row_error(path, line_number, "limitation must be positive")
        return limit

    def _parse_vehicles(
        self,
        path: Path,
        constraints: tuple[RatioConstraint, ...],
    ) -> tuple[tuple[Vehicle, ...], tuple[Vehicle, ...]]:
        header, rows = self._read_table(path)
        expected_header = (
            "Date",
            "SeqRank",
            "Ident",
            "Paint Color",
            *(item.ident for item in constraints),
        )
        self._require_header(path, header, expected_header)

        vehicles: list[Vehicle] = []
        dates: list[str] = []
        for line_number, row in rows:
            date, rank_text, ident, color, *flag_texts = row
            if date not in dates:
                dates.append(date)
            try:
                invalid_flags = [value for value in flag_texts if value not in {"0", "1"}]
                if invalid_flags:
                    raise ValueError(f"option flags must be 0 or 1, got {invalid_flags[0]!r}")
                vehicles.append(
                    Vehicle(
                        ident=ident,
                        production_date=date,
                        original_rank=int(rank_text),
                        paint_color=color,
                        option_flags=tuple(value == "1" for value in flag_texts),
                    )
                )
            except (ValueError, DomainValidationError) as exc:
                raise self._row_error(path, line_number, str(exc)) from exc

        # 官方文件按 D-1、D 的业务顺序出现，不能按日期字符串自行排序。
        if len(dates) != 2:
            raise InstanceFormatError(
                f"{path.name} must contain exactly two production dates (D-1 and D); "
                f"found {len(dates)}"
            )
        previous_day = tuple(vehicle for vehicle in vehicles if vehicle.production_date == dates[0])
        planning_day = tuple(vehicle for vehicle in vehicles if vehicle.production_date == dates[1])
        return previous_day, planning_day

    @staticmethod
    def _read_table(path: Path) -> tuple[tuple[str, ...], list[tuple[int, tuple[str, ...]]]]:
        """读取官方分号表；兼容 UTF-8 BOM、空行和每行末尾的分号。"""

        if not path.is_file():
            raise InstanceFormatError(f"Required file does not exist: {path}")
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                raw_rows = list(csv.reader(stream, delimiter=";"))
        except (OSError, UnicodeError) as exc:
            raise InstanceFormatError(f"Cannot read {path}: {exc}") from exc

        normalized: list[tuple[int, tuple[str, ...]]] = []
        for line_number, raw_row in enumerate(raw_rows, start=1):
            cells = [cell.strip() for cell in raw_row]
            while cells and cells[-1] == "":
                cells.pop()
            if cells:
                normalized.append((line_number, tuple(cells)))

        if not normalized:
            raise InstanceFormatError(f"{path.name} is empty")

        _, header = normalized[0]
        rows = normalized[1:]
        for line_number, row in rows:
            if len(row) != len(header):
                raise InstanceFormatError(
                    f"{path.name}:{line_number}: expected {len(header)} fields, got {len(row)}"
                )
        return header, rows

    @staticmethod
    def _require_header(path: Path, actual: tuple[str, ...], expected: tuple[str, ...]) -> None:
        if actual != expected:
            raise InstanceFormatError(
                f"Unexpected header in {path.name}: expected {expected}, got {actual}"
            )

    @staticmethod
    def _row_error(path: Path, line_number: int, message: str) -> InstanceFormatError:
        return InstanceFormatError(f"{path.name}:{line_number}: {message}")
