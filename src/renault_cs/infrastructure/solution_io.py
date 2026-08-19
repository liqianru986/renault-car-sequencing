"""解文件 I/O：读写官方无表头的 ``Sequence rank;Identifier`` 格式。"""

from __future__ import annotations

import csv
from pathlib import Path
from uuid import uuid4

from renault_cs.domain.exceptions import InvalidSolutionError
from renault_cs.domain.solution import SequenceSolution


class RoadefSolutionWriter:
    """将内存中的 D 日车辆排列原子写入官方解文件。"""

    def write(self, solution: SequenceSolution, output_path: Path) -> None:
        """按 1 基连续 rank 写出解；写入成功前不破坏已有结果。"""

        if not solution.vehicle_ids:
            raise InvalidSolutionError("Cannot write an empty vehicle sequence")
        if len(solution.vehicle_ids) != len(set(solution.vehicle_ids)):
            raise InvalidSolutionError("Cannot write a sequence containing duplicate vehicles")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
        try:
            with temporary_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, delimiter=";", lineterminator="\n")
                for rank, vehicle_id in enumerate(solution.vehicle_ids, start=1):
                    writer.writerow((rank, vehicle_id))
            temporary_path.replace(output_path)
        finally:
            temporary_path.unlink(missing_ok=True)


class RoadefSolutionReader:
    """读取官方解文件，供 Checker 对齐和结果复用。"""

    def read(self, path: Path, *, instance_name: str, algorithm: str) -> SequenceSolution:
        """验证连续 rank 后返回标准 ``SequenceSolution``。"""

        path = Path(path)
        vehicle_ids: list[str] = []
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                rows = csv.reader(stream, delimiter=";")
                for line_number, row in enumerate(rows, start=1):
                    if not row or all(not cell.strip() for cell in row):
                        continue
                    cells = [cell.strip() for cell in row]
                    while cells and cells[-1] == "":
                        cells.pop()
                    if len(cells) != 2:
                        raise InvalidSolutionError(
                            f"{path.name}:{line_number}: expected 2 fields, got {len(cells)}"
                        )
                    expected_rank = len(vehicle_ids) + 1
                    try:
                        actual_rank = int(cells[0])
                    except ValueError as exc:
                        raise InvalidSolutionError(
                            f"{path.name}:{line_number}: sequence rank must be an integer"
                        ) from exc
                    if actual_rank != expected_rank:
                        raise InvalidSolutionError(
                            f"{path.name}:{line_number}: expected rank {expected_rank}, "
                            f"got {actual_rank}"
                        )
                    vehicle_ids.append(cells[1])
        except OSError as exc:
            raise InvalidSolutionError(f"Cannot read solution {path}: {exc}") from exc

        return SequenceSolution(
            instance_name=instance_name,
            vehicle_ids=tuple(vehicle_ids),
            algorithm=algorithm,
        )
