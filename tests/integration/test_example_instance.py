"""SeqRank 集成测试：验证车辆排序、统一评分和官方格式往返。"""

from pathlib import Path

from renault_cs.algorithms.seqrank import SeqRankSolver
from renault_cs.application.config import SolveConfig
from renault_cs.domain.enums import ObjectiveKind
from renault_cs.domain.models import ObjectiveSpec, ProblemInstance, Vehicle
from renault_cs.domain.solution import SequenceSolution
from renault_cs.evaluation.evaluator import RenaultEvaluator
from renault_cs.infrastructure.solution_io import RoadefSolutionReader, RoadefSolutionWriter


def test_seqrank_solver_orders_and_evaluates_complete_solution() -> None:
    vehicles = (
        Vehicle("V3", "D", 30, "BLUE", ()),
        Vehicle("V1", "D", 10, "RED", ()),
        Vehicle("V2", "D", 20, "RED", ()),
    )
    instance = ProblemInstance(
        name="tiny",
        paint_batch_limit=2,
        ratio_constraints=(),
        objectives=(ObjectiveSpec(1, ObjectiveKind.PAINT_COLOR_CHANGES, "paint"),),
        previous_day_vehicles=(Vehicle("P", "D-1", 1, "BLUE", ()),),
        planning_day_vehicles=vehicles,
    )

    result = SeqRankSolver(RenaultEvaluator()).solve(
        instance,
        SolveConfig(time_limit_sec=1.0),
    )

    assert result.status == "completed"
    assert result.solution.vehicle_ids == ("V1", "V2", "V3")
    assert result.solution.algorithm == "seqrank"
    assert result.solution.seed is None
    assert result.evaluation is not None
    assert result.evaluation.is_feasible is True
    assert result.evaluation.paint_changes == 2
    assert result.evaluation.objective_vector == (2,)


def test_solution_writer_round_trip(tmp_path: Path) -> None:
    solution_path = tmp_path / "solution.txt"
    vehicle_ids = ("V1", "V2", "V3")
    solution = SequenceSolution("tiny", vehicle_ids, "seqrank")
    RoadefSolutionWriter().write(solution, solution_path)
    loaded = RoadefSolutionReader().read(
        solution_path,
        instance_name="tiny",
        algorithm="roundtrip",
    )

    assert solution_path.read_text(encoding="utf-8") == "1;V1\n2;V2\n3;V3\n"
    assert loaded.vehicle_ids == vehicle_ids
