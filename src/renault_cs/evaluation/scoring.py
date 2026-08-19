"""多目标评分：按 instance 的目标 rank 构建向量及 Checker 加权分。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from renault_cs.domain.enums import ObjectiveKind
from renault_cs.domain.models import ObjectiveSpec

CHECKER_OBJECTIVE_SLOTS = 3


def build_objective_vector(
    objectives: Sequence[ObjectiveSpec],
    values: Mapping[ObjectiveKind, int],
) -> tuple[int, ...]:
    """按官方 rank 返回字典序目标向量，避免在算法中写死目标顺序。"""

    return tuple(values[objective.kind] for objective in sorted(objectives, key=lambda x: x.rank))


def calculate_weighted_score(
    objective_vector: Sequence[int],
    *,
    base: int = 100,
    objective_slots: int = CHECKER_OBJECTIVE_SLOTS,
) -> int:
    """按 Checker 固定目标槽位计算位权分；缺失的低位目标不推动权重左移。"""

    if base <= 1:
        raise ValueError("score base must be greater than 1")
    if len(objective_vector) > objective_slots:
        raise ValueError("objective vector is longer than available Checker slots")
    return sum(
        value * base ** (objective_slots - index - 1)
        for index, value in enumerate(objective_vector)
    )
