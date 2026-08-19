"""Windows Checker 适配器：隔离历史可执行文件、临时目录和报告解析。"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from renault_cs.domain.exceptions import CheckerExecutionError


@dataclass(frozen=True, slots=True)
class CheckerReport:
    """官方 Checker 候选解报告中的核心指标。"""

    is_valid: bool
    score: int
    paint_changes: int
    hprc_violations: int
    lprc_violations: int
    report_text: str


class WindowsOfficialChecker:
    """在隔离临时目录中运行 ROADEF 随包 Windows Checker。"""

    def __init__(self, executable: Path, *, timeout_sec: float = 30.0) -> None:
        self._executable = Path(executable)
        self._timeout_sec = timeout_sec

    def check(self, instance_dir: Path, solution_file: Path) -> CheckerReport:
        """复制只读输入、执行 Checker，并解析候选解报告。"""

        instance_dir = Path(instance_dir)
        solution_file = Path(solution_file)
        if not self._executable.is_file():
            raise CheckerExecutionError(f"Checker executable does not exist: {self._executable}")
        if not instance_dir.is_dir():
            raise CheckerExecutionError(f"Instance directory does not exist: {instance_dir}")
        if not solution_file.is_file():
            raise CheckerExecutionError(f"Solution file does not exist: {solution_file}")

        with tempfile.TemporaryDirectory(prefix="renault_checker_") as temporary_dir:
            workspace = Path(temporary_dir)
            instances_root = workspace / "instances"
            solutions_root = workspace / "solutions"
            logs_root = workspace / "logs"
            copied_instance = instances_root / instance_dir.name
            solutions_root.mkdir(parents=True)
            logs_root.mkdir(parents=True)
            shutil.copytree(instance_dir, copied_instance)
            shutil.copy2(solution_file, solutions_root / instance_dir.name)
            shutil.copy2(self._executable, workspace / "exeCarSeq.exe")
            (workspace / "exeCarSeq.ini").write_text(
                self._build_ini(instance_dir.name, logs_root),
                encoding="ascii",
            )

            try:
                process = subprocess.run(
                    [str(workspace / "exeCarSeq.exe")],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_sec,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise CheckerExecutionError(f"Official Checker execution failed: {exc}") from exc

            report_path = solutions_root / (
                f"{instance_dir.name}_reporting_candidate_solution.txt"
            )
            if process.returncode != 0 or not report_path.is_file():
                diagnostic = (process.stderr or process.stdout).strip()
                raise CheckerExecutionError(
                    f"Official Checker returned {process.returncode}: {diagnostic or 'no report'}"
                )
            try:
                report_text = report_path.read_text(encoding="latin-1")
            except OSError as exc:
                raise CheckerExecutionError(f"Cannot read Checker report: {exc}") from exc
            return self._parse_report(report_text)

    @staticmethod
    def _build_ini(instance_name: str, logs_root: Path) -> str:
        log_path = f"{logs_root}\\"
        return (
            "S_CARSEQ_RACINE_PATH_NAME=instances\\\n"
            f"S_CARSEQ_LOGS_DIRECTORY={log_path}\n"
            "S_CARSEQ_RESULT_DIRECTORY=solutions\\\n\n"
            f"S_CARSEQ_INSTANCE_PB_NAME={instance_name}\n"
            "S_CARSEQ_CANDIDATE_NAME=codex\n"
            "S_CARSEQ_EXE_LOGFILE_FILE_NAME=exeCarSeq.log\n\n"
            "S_CARSEQ_ECARTS_CRITERES_FILE_NAME=ratios.txt\n"
            "S_CARSEQ_LONG_MAX_RAF_TEINTES_FILE_NAME=paint_batch_limit.txt\n"
            "S_CARSEQ_OBJECTIF_OPTIM_FILE_NAME=optimization_objectives.txt\n"
            "S_CARSEQ_VEHICULES_FILE_NAME=vehicles.txt\n\n"
            "S_CARSEQ_INDIC_SEQ_INITIAL_SUFFIX_NAME=_reporting_reference_solution.txt\n"
            "S_CARSEQ_INDIC_SEQ_FINAL_SUFFIX_NAME=_reporting_candidate_solution.txt\n"
        )

    @staticmethod
    def _parse_report(report_text: str) -> CheckerReport:
        def integer(pattern: str, label: str) -> int:
            match = re.search(pattern, report_text)
            if match is None:
                raise CheckerExecutionError(f"Cannot parse {label} from Checker report")
            return int(round(float(match.group(1))))

        score = integer(r"Mark of the solution\s*=\s*([0-9.]+)", "score")
        hprc = integer(
            r"Total number of violations of high priority level ratio constraints\s*=\s*(\d+)",
            "HPRC violations",
        )
        lprc = integer(
            r"Total number of violations of low priority level ratio constraints\s*=\s*(\d+)",
            "LPRC violations",
        )
        paint = integer(r"Total number of paint color changes\s*=\s*(\d+)", "paint changes")
        return CheckerReport(
            is_valid=True,
            score=score,
            paint_changes=paint,
            hprc_violations=hprc,
            lprc_violations=lprc,
            report_text=report_text,
        )
