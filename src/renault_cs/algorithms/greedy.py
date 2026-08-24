"""目标感知贪心构造：按车型聚合逐位生成可行的初始序列。"""

from __future__ import annotations

from collections import defaultdict

from renault_cs.domain.enums import ObjectiveKind
from renault_cs.domain.models import ProblemInstance, Vehicle


def construct_greedy_sequence(
    instance: ProblemInstance,
    rng: object | None = None,
) -> tuple[str, ...]:
    """按实例目标优先级选择下一车型，并用 SeqRank 还原真实车辆。"""

    groups: dict[tuple[str, tuple[bool, ...]], list[Vehicle]] = defaultdict(list)
    for vehicle in instance.planning_day_vehicles:
        groups[(vehicle.paint_color, vehicle.option_flags)].append(vehicle)
    for vehicles in groups.values():
        vehicles.sort(key=lambda item: (item.original_rank, item.ident), reverse=True)

    history = list(instance.previous_day_vehicles)
    result: list[str] = []
    last_color = history[-1].paint_color if history else None
    same_color_run = _trailing_color_run(history)

    while groups:
        remaining_count = sum(len(vehicles) for vehicles in groups.values())
        remaining_options = [0] * len(instance.ratio_constraints)
        for (_, flags), vehicles in groups.items():
            for index, flag in enumerate(flags):
                remaining_options[index] += int(flag) * len(vehicles)
        feasible_keys = [
            key
            for key in groups
            if key[0] != last_color or same_color_run < instance.paint_batch_limit
        ]
        candidates = feasible_keys or list(groups)
        scored = [
            (
                _candidate_key(
                    instance,
                    history,
                    key,
                    last_color,
                    remaining_options,
                    remaining_count,
                ),
                key,
            )
            for key in candidates
        ]
        _, selected = min(scored, key=lambda item: item[0])
        vehicle = groups[selected].pop()
        if not groups[selected]:
            del groups[selected]

        result.append(vehicle.ident)
        history.append(vehicle)
        if vehicle.paint_color == last_color:
            same_color_run += 1
        else:
            last_color = vehicle.paint_color
            same_color_run = 1

    return tuple(result)


def _candidate_key(
    instance: ProblemInstance,
    history: list[Vehicle],
    vehicle_type: tuple[str, tuple[bool, ...]],
    last_color: str | None,
    remaining_options: list[int],
    remaining_count: int,
) -> tuple[object, ...]:
    color, flags = vehicle_type
    hprc = lprc = 0
    for index, constraint in enumerate(instance.ratio_constraints):
        previous_flags = [
            int(vehicle.option_flags[index])
            for vehicle in history[-(constraint.denominator - 1):]
        ] if constraint.denominator > 1 else []
        violation = max(0, sum(previous_flags) + int(flags[index]) - constraint.numerator)
        if constraint.is_high_priority:
            hprc += violation
        else:
            lprc += violation

    values: dict[ObjectiveKind, int] = {
        ObjectiveKind.HPRC_VIOLATIONS: hprc,
        ObjectiveKind.LPRC_VIOLATIONS: lprc,
        ObjectiveKind.PAINT_COLOR_CHANGES: int(last_color is not None and color != last_color),
    }
    urgency = {ObjectiveKind.HPRC_VIOLATIONS: 0.0, ObjectiveKind.LPRC_VIOLATIONS: 0.0}
    for index, (flag, constraint) in enumerate(
        zip(flags, instance.ratio_constraints, strict=True)
    ):
        if not flag:
            continue
        pressure = (
            remaining_options[index]
            / max(1, remaining_count)
            / max(constraint.ratio, 1e-9)
        )
        kind = (
            ObjectiveKind.HPRC_VIOLATIONS
            if constraint.is_high_priority
            else ObjectiveKind.LPRC_VIOLATIONS
        )
        urgency[kind] += pressure

    objective_key: list[float] = []
    for objective in instance.objectives:
        objective_key.append(float(values[objective.kind]))
        if objective.kind in urgency:
            objective_key.append(-urgency[objective.kind])
    difficulty = sum(
        int(flag) * constraint.denominator / max(1, constraint.numerator)
        for flag, constraint in zip(flags, instance.ratio_constraints, strict=True)
    )
    return (*objective_key, -difficulty, color, flags)


def _trailing_color_run(vehicles: list[Vehicle]) -> int:
    if not vehicles:
        return 0
    color = vehicles[-1].paint_color
    run = 0
    for vehicle in reversed(vehicles):
        if vehicle.paint_color != color:
            break
        run += 1
    return run
