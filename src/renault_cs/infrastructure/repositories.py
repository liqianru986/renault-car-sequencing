"""本地结果仓储：原子持久化 JSON、Checker 报告和 Benchmark CSV。"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from renault_cs.benchmark.records import BenchmarkRecord


class LocalResultRepository:
    """统一管理一次实验的目录与机器可读产物。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.solutions_dir = self.root / "solutions"
        self.checker_reports_dir = self.root / "checker_reports"
        self.root.mkdir(parents=True, exist_ok=True)
        self.solutions_dir.mkdir(exist_ok=True)
        self.checker_reports_dir.mkdir(exist_ok=True)

    def solution_path(self, instance_name: str, algorithm: str, seed: int | None) -> Path:
        seed_suffix = "" if seed is None else f"_seed-{seed}"
        return self.solutions_dir / f"{instance_name}_{algorithm}{seed_suffix}.txt"

    def save_checker_report(self, name: str, report_text: str) -> Path:
        path = self.checker_reports_dir / f"{name}.txt"
        self._atomic_write_text(path, report_text)
        return path

    def save_json(self, name: str, payload: object) -> Path:
        path = self.root / f"{name}.json"
        self._atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        )
        return path

    def save_benchmark_records(self, records: list[BenchmarkRecord]) -> Path:
        path = self.root / "benchmark_records.csv"
        rows = [asdict(record) for record in records]
        if not rows:
            self._atomic_write_text(path, "")
            return path
        rows = [
            {**row, "objective_vector": "|".join(map(str, row["objective_vector"]))}
            for row in rows
        ]
        temporary = self._temporary_path(path)
        try:
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    @classmethod
    def _atomic_write_text(cls, path: Path, content: str) -> None:
        temporary = cls._temporary_path(path)
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _temporary_path(path: Path) -> Path:
        return path.with_name(f".{path.name}.{uuid4().hex}.tmp")
