"""增量评分必须与完整评分在交换和插入候选上严格一致。"""

from __future__ import annotations

from pathlib import Path
import random

from renault_cs.algorithms.alns import AlnsSolver
from renault_cs.evaluation.evaluator import RenaultEvaluator
from renault_cs.evaluation.incremental import IncrementalEvaluationState
from renault_cs.infrastructure.roadef_parser import RoadefParser


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTANCE_PATH = (
    PROJECT_ROOT.parent
    / "Instances_set_X"
    / "Instances_set_X"
    / "028_CH2_EP_ENP_RAF_S51_J1"
)


def test_incremental_adjacent_swaps_match_full_evaluation() -> None:
    instance = RoadefParser().parse(INSTANCE_PATH)
    solver = AlnsSolver(RenaultEvaluator())
    base = [
        vehicle.ident
        for vehicle in sorted(
            instance.planning_day_vehicles,
            key=lambda vehicle: (vehicle.original_rank, vehicle.ident),
        )
    ]
    state = IncrementalEvaluationState(instance, base)

    for left in (0, 1, 10, 31, len(base) - 2):
        state.swap(left, left + 1)
        incremental = state.score()
        full_score, full_feasible = solver._partial_score(instance, list(state.vehicle_ids))
        assert incremental.score == full_score
        assert incremental.paint_feasible == full_feasible


def test_incremental_insert_positions_match_full_evaluation() -> None:
    instance = RoadefParser().parse(INSTANCE_PATH)
    solver = AlnsSolver(RenaultEvaluator())
    full = [
        vehicle.ident
        for vehicle in sorted(
            instance.planning_day_vehicles,
            key=lambda vehicle: (vehicle.original_rank, vehicle.ident),
        )
    ]
    vehicle_id = full.pop(17)
    state = IncrementalEvaluationState(instance, [vehicle_id, *full])

    for position in (0, 1, 7, 17, 32, len(full)):
        current = state.vehicle_ids.index(vehicle_id)
        while current < position:
            state.swap(current, current + 1)
            current += 1
        incremental = state.score()
        candidate = full.copy()
        candidate.insert(position, vehicle_id)
        full_score, full_feasible = solver._partial_score(instance, candidate)
        assert incremental.score == full_score
        assert incremental.paint_feasible == full_feasible


def test_incremental_swap_insert_and_reflection_match_full_evaluation() -> None:
    instance = RoadefParser().parse(INSTANCE_PATH)
    solver = AlnsSolver(RenaultEvaluator())
    base = [
        vehicle.ident
        for vehicle in sorted(
            instance.planning_day_vehicles,
            key=lambda vehicle: (vehicle.original_rank, vehicle.ident),
        )
    ]
    state = IncrementalEvaluationState(instance, base)

    moves = (
        (state.swap, (2, 29)),
        (state.insert, (5, 24)),
        (state.insert, (27, 4)),
        (state.reflect, (8, 31)),
        (state.reflect, (0, 9)),
    )
    for move, arguments in moves:
        move(*arguments)
        incremental = state.score()
        full_score, full_feasible = solver._partial_score(
            instance, list(state.vehicle_ids)
        )
        assert incremental.score == full_score
        assert incremental.paint_feasible == full_feasible


def test_random_incremental_move_chain_matches_full_evaluation() -> None:
    instance = RoadefParser().parse(INSTANCE_PATH)
    solver = AlnsSolver(RenaultEvaluator())
    rng = random.Random(2026)
    base = [
        vehicle.ident
        for vehicle in sorted(
            instance.planning_day_vehicles,
            key=lambda vehicle: (vehicle.original_rank, vehicle.ident),
        )
    ]
    state = IncrementalEvaluationState(instance, base)

    for _ in range(200):
        left, right = rng.sample(range(len(base)), 2)
        move = rng.choice((state.swap, state.insert, state.reflect))
        move(left, right)
        incremental = state.score()
        full_score, full_feasible = solver._partial_score(
            instance, list(state.vehicle_ids)
        )
        assert incremental.score == full_score
        assert incremental.paint_feasible == full_feasible
