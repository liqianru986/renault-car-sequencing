"""解合法性测试：覆盖重复、未知车辆和 instance 不匹配。"""

from renault_cs.domain.enums import ObjectiveKind
from renault_cs.domain.models import ObjectiveSpec, ProblemInstance, Vehicle
from renault_cs.domain.solution import SequenceSolution
from renault_cs.evaluation.evaluator import RenaultEvaluator


def test_evaluator_reports_structural_solution_errors() -> None:
    vehicle = Vehicle("A", "D", 1, "RED", ())
    instance = ProblemInstance(
        name="example",
        paint_batch_limit=2,
        ratio_constraints=(),
        objectives=(ObjectiveSpec(1, ObjectiveKind.PAINT_COLOR_CHANGES, "paint"),),
        previous_day_vehicles=(),
        planning_day_vehicles=(vehicle,),
    )
    solution = SequenceSolution(
        instance_name="wrong",
        vehicle_ids=("UNKNOWN", "UNKNOWN"),
        algorithm="test",
    )

    result = RenaultEvaluator().evaluate(instance, solution)

    assert result.is_feasible is False
    assert any("Instance name mismatch" in error for error in result.validation_errors)
    assert any("Duplicate" in error for error in result.validation_errors)
    assert any("Unknown" in error for error in result.validation_errors)
    assert any("Missing" in error for error in result.validation_errors)
