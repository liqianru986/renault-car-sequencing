"""Exact 数据测试：验证车辆聚合、跨日常数、尾部窗口和解还原。"""

from renault_cs.domain.enums import HprcDifficulty, ObjectiveKind, RatioPriority
from renault_cs.domain.models import ObjectiveSpec, ProblemInstance, RatioConstraint, Vehicle
from renault_cs.exact.model_data import build_exact_model_data
from renault_cs.exact.vehicle_types import reconstruct_vehicle_ids


def _instance() -> ProblemInstance:
    constraint = RatioConstraint("HPRC1", 1, 3, RatioPriority.HIGH)
    return ProblemInstance(
        name="tiny_exact",
        paint_batch_limit=2,
        ratio_constraints=(constraint,),
        objectives=(
            ObjectiveSpec(
                1,
                ObjectiveKind.HPRC_VIOLATIONS,
                "high_priority_level_and_easy_to_satisfy_ratio_constraints",
                HprcDifficulty.EASY,
            ),
            ObjectiveSpec(2, ObjectiveKind.PAINT_COLOR_CHANGES, "paint_color_batches"),
        ),
        previous_day_vehicles=(
            Vehicle("P1", "D-1", 1, "BLUE", (False,)),
            Vehicle("P2", "D-1", 2, "RED", (True,)),
        ),
        planning_day_vehicles=(
            Vehicle("V2", "D", 2, "RED", (True,)),
            Vehicle("V1", "D", 1, "RED", (True,)),
            Vehicle("V3", "D", 3, "BLUE", (False,)),
        ),
    )


def test_model_data_aggregates_equivalent_vehicles() -> None:
    data = build_exact_model_data(_instance())

    assert data.position_count == 3
    assert len(data.vehicle_types) == 2
    red_type = next(item for item in data.vehicle_types if item.paint_color == "RED")
    assert red_type.count == 2
    assert red_type.vehicle_ids == ("V1", "V2")


def test_model_data_precomputes_bks_and_tail_windows() -> None:
    data = build_exact_model_data(_instance())
    windows = {item.start_position: item for item in data.ratio_windows}

    assert tuple(windows) == (-2, -1, 0, 1, 2)
    assert windows[-2].previous_day_count == 1
    assert windows[-2].planning_positions == (0,)
    assert windows[-1].previous_day_count == 1
    assert windows[-1].planning_positions == (0, 1)
    assert windows[0].previous_day_count == 0
    assert windows[2].planning_positions == (2,)


def test_reconstruct_vehicle_ids_uses_stable_type_queues() -> None:
    data = build_exact_model_data(_instance())
    red = next(item.index for item in data.vehicle_types if item.paint_color == "RED")
    blue = next(item.index for item in data.vehicle_types if item.paint_color == "BLUE")

    assert reconstruct_vehicle_ids((red, blue, red), data.vehicle_types) == ("V1", "V3", "V2")
