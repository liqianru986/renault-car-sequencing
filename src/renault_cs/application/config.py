"""应用配置模型：为单次求解和批量 Benchmark 提供强类型运行参数。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from renault_cs.domain.exceptions import DomainValidationError


@dataclass(frozen=True, slots=True)
class SolveConfig:
    """单实例求解配置，不包含具体算法的强制依赖。"""

    time_limit_sec: float
    seed: int = 42
    log_interval_sec: float = 1.0
    max_iterations: int | None = None
    algorithm_parameters: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.time_limit_sec <= 0:
            raise DomainValidationError("time_limit_sec must be positive")
        if self.log_interval_sec <= 0:
            raise DomainValidationError("log_interval_sec must be positive")
        if self.max_iterations is not None and self.max_iterations <= 0:
            raise DomainValidationError("max_iterations must be positive when provided")
        object.__setattr__(
            self,
            "algorithm_parameters",
            MappingProxyType(dict(self.algorithm_parameters)),
        )


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """多实例、多算法和多随机种子实验的统一配置。"""

    time_limit_sec: float
    seeds: tuple[int, ...]
    output_dir: Path
    parallel_workers: int = 1
    run_official_checker: bool = True
    keep_all_solutions: bool = True
    algorithm_parameters: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.time_limit_sec <= 0:
            raise DomainValidationError("Benchmark time_limit_sec must be positive")
        if not self.seeds:
            raise DomainValidationError("Benchmark seeds cannot be empty")
        if self.parallel_workers <= 0:
            raise DomainValidationError("parallel_workers must be positive")
        object.__setattr__(
            self,
            "algorithm_parameters",
            MappingProxyType(dict(self.algorithm_parameters)),
        )
