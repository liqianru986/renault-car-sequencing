"""Checker 报告解析测试：确认官方核心指标转换为结构化结果。"""

from renault_cs.infrastructure.checker_adapter import WindowsOfficialChecker


def test_checker_report_parser_extracts_objectives() -> None:
    report = WindowsOfficialChecker._parse_report(
        """
        Mark of the solution = 13299.000000
        Total number of violations of high priority level ratio constraints = 0
        Total number of violations of low priority level ratio constraints = 99
        Total number of paint color changes = 132
        """
    )

    assert report.is_valid is True
    assert (report.hprc_violations, report.paint_changes, report.lprc_violations) == (0, 132, 99)
    assert report.score == 13_299
