"""ROADEF Parser 单元测试：覆盖正常建对象、格式兼容与关键异常。"""

from pathlib import Path

import pytest

from renault_cs.domain.enums import HprcDifficulty, ObjectiveKind, RatioPriority
from renault_cs.domain.exceptions import InstanceFormatError
from renault_cs.infrastructure.roadef_parser import RoadefParser


def _write_instance(root: Path, *, vehicle_flag: str = "1") -> Path:
    root.mkdir()
    (root / "ratios.txt").write_text(
        "Ratio;Prio;Ident;\n1/2;1;HPRC1;\n1/3;0;LPRC1;\n",
        encoding="utf-8",
    )
    (root / "optimization_objectives.txt").write_text(
        "rank;objective name;\n"
        "1;high_priority_level_and_easy_to_satisfy_ratio_constraints;\n"
        "2;paint_color_batches;\n"
        "3;low_priority_level_ratio_constraints;\n",
        encoding="utf-8",
    )
    (root / "paint_batch_limit.txt").write_text(
        "\ufefflimitation;\n15;\n",
        encoding="utf-8",
    )
    (root / "vehicles.txt").write_text(
        "Date;SeqRank;Ident;Paint Color;HPRC1;LPRC1\n"
        "2003 26 1;8;V-D1;RED;0;1\n"
        f"2003 26 2;1;V-D-01;BLUE;{vehicle_flag};0\n"
        "2003 26 2;2;V-D-02;RED;0;1\n",
        encoding="utf-8",
    )
    return root


def test_parse_builds_complete_problem_instance(tmp_path: Path) -> None:
    instance = RoadefParser().parse(_write_instance(tmp_path / "example"))

    assert instance.name == "example"
    assert instance.paint_batch_limit == 15
    assert len(instance.previous_day_vehicles) == 1
    assert len(instance.planning_day_vehicles) == 2

    hprc, lprc = instance.ratio_constraints
    assert (hprc.numerator, hprc.denominator, hprc.priority) == (1, 2, RatioPriority.HIGH)
    assert lprc.priority is RatioPriority.LOW

    first_vehicle = instance.planning_day_vehicles[0]
    assert first_vehicle.ident == "V-D-01"
    assert first_vehicle.option_flags == (True, False)
    assert instance.has_option(first_vehicle, "HPRC1") is True

    first_objective = instance.objectives[0]
    assert first_objective.kind is ObjectiveKind.HPRC_VIOLATIONS
    assert first_objective.hprc_difficulty is HprcDifficulty.EASY


def test_parse_rejects_non_binary_vehicle_flag(tmp_path: Path) -> None:
    instance_dir = _write_instance(tmp_path / "bad_flag", vehicle_flag="2")

    with pytest.raises(InstanceFormatError, match="option flags must be 0 or 1"):
        RoadefParser().parse(instance_dir)


def test_parse_rejects_missing_official_file(tmp_path: Path) -> None:
    instance_dir = _write_instance(tmp_path / "missing_file")
    (instance_dir / "ratios.txt").unlink()

    with pytest.raises(InstanceFormatError, match="ratios.txt"):
        RoadefParser().parse(instance_dir)
