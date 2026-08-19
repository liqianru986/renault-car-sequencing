"""Gurobi 聚合 MILP：构建模型、注入 MIP Start、求解并还原车辆序列。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from renault_cs.application.config import SolveConfig
from renault_cs.domain.enums import ObjectiveKind, RatioPriority
from renault_cs.domain.exceptions import SolverUnavailableError
from renault_cs.domain.models import ProblemInstance
from renault_cs.domain.solution import SequenceSolution, SolveResult
from renault_cs.exact.base import ExactSolveMetrics, ExactSolveStatus
from renault_cs.exact.model_data import ExactModelData, build_exact_model_data
from renault_cs.exact.vehicle_types import reconstruct_vehicle_ids, vehicle_to_type_index
from renault_cs.evaluation.scoring import CHECKER_OBJECTIVE_SLOTS

if TYPE_CHECKING:
    from renault_cs.application.ports import SolutionEvaluator


@dataclass(slots=True)
class _ModelVariables:
    """集中保存变量句柄，避免通过字符串从 Gurobi 模型反查。"""

    x: Any
    paint_color: Any
    paint_change: Any
    ratio_violation: Any


class GurobiExactSolver:
    """基于聚合车辆类型的 ROADEF 2005 MILP 求解器。"""

    def __init__(
        self,
        evaluator: SolutionEvaluator,
        *,
        checker_score_base: int = 100,
    ) -> None:
        if checker_score_base <= 1:
            raise ValueError("checker_score_base must be greater than 1")
        self._evaluator = evaluator
        self._score_base = checker_score_base

    @property
    def name(self) -> str:
        return "gurobi_exact"

    def solve(self, instance: ProblemInstance, config: SolveConfig) -> SolveResult:
        """构建并求解加权MILP；无Incumbent时返回无解状态而不伪造序列。"""

        gp, grb = self._import_gurobi()
        data = build_exact_model_data(instance)
        try:
            model = gp.Model(f"renault_cs_{instance.name}")
        except gp.GurobiError as exc:
            raise SolverUnavailableError(f"Cannot start Gurobi: {exc}") from exc

        try:
            self._configure_model(model, config)
            variables = self._build_model(gp, grb, model, instance, data)
            if bool(config.algorithm_parameters.get("use_mip_start", True)):
                self._set_seqrank_mip_start(instance, data, variables)
            self._write_debug_artifacts(model, config)
            model.optimize()

            metrics = self._collect_metrics(model, grb)
            if metrics.solution_count == 0:
                return SolveResult(
                    solution=SequenceSolution(
                        instance_name=instance.name,
                        vehicle_ids=(),
                        algorithm=self.name,
                        runtime_sec=metrics.runtime_sec,
                        seed=config.seed,
                        metadata=self._metrics_metadata(metrics),
                    ),
                    evaluation=None,
                    status=metrics.status.value,
                    message="Gurobi finished without a feasible incumbent",
                )

            type_sequence = self._extract_type_sequence(data, variables)
            vehicle_ids = reconstruct_vehicle_ids(type_sequence, data.vehicle_types)
            solution = SequenceSolution(
                instance_name=instance.name,
                vehicle_ids=vehicle_ids,
                algorithm=self.name,
                runtime_sec=metrics.runtime_sec,
                seed=config.seed,
                metadata={
                    **self._metrics_metadata(metrics),
                    "vehicle_type_count": len(data.vehicle_types),
                    "binary_assignment_count": len(data.vehicle_types) * data.position_count,
                    "objective_mode": "checker_weighted",
                    "checker_score_base": self._score_base,
                    "mip_start": bool(config.algorithm_parameters.get("use_mip_start", True)),
                },
            )
            evaluation = self._evaluator.evaluate(instance, solution)
            self._validate_extracted_solution(evaluation, metrics)
            return SolveResult(
                solution=solution,
                evaluation=evaluation,
                status=metrics.status.value,
            )
        finally:
            model.dispose()

    @staticmethod
    def _import_gurobi() -> tuple[Any, Any]:
        try:
            import gurobipy as gp
            from gurobipy import GRB
        except (ImportError, OSError) as exc:
            raise SolverUnavailableError(
                "gurobipy is not available; install the optional 'gurobi' dependency"
            ) from exc
        return gp, GRB

    @staticmethod
    def _configure_model(model: Any, config: SolveConfig) -> None:
        parameters = config.algorithm_parameters
        model.Params.TimeLimit = config.time_limit_sec
        model.Params.Seed = config.seed
        model.Params.MIPGap = float(parameters.get("mip_gap", 0.0))
        model.Params.Threads = int(parameters.get("threads", 0))
        model.Params.OutputFlag = int(bool(parameters.get("log_to_console", True)))
        log_file = parameters.get("gurobi_log_file")
        if log_file:
            model.Params.LogFile = str(log_file)

    @staticmethod
    def _write_debug_artifacts(model: Any, config: SolveConfig) -> None:
        model_file = config.algorithm_parameters.get("model_file")
        if model_file:
            model.write(str(model_file))
        mip_start_file = config.algorithm_parameters.get("mip_start_file")
        if mip_start_file and bool(config.algorithm_parameters.get("use_mip_start", True)):
            model.write(str(mip_start_file))

    def _build_model(
        self,
        gp: Any,
        grb: Any,
        model: Any,
        instance: ProblemInstance,
        data: ExactModelData,
    ) -> _ModelVariables:
        type_indices = range(len(data.vehicle_types))
        positions = range(data.position_count)

        x = model.addVars(type_indices, positions, vtype=grb.BINARY, name="x")
        paint_color = model.addVars(data.colors, positions, vtype=grb.BINARY, name="p")
        paint_change = model.addVars(positions, vtype=grb.BINARY, name="d")
        ratio_violation = model.addVars(
            range(len(data.ratio_windows)),
            lb=0.0,
            vtype=grb.CONTINUOUS,
            name="u",
        )
        variables = _ModelVariables(x, paint_color, paint_change, ratio_violation)

        self._add_assignment_constraints(gp, model, data, variables)
        self._add_paint_constraints(gp, model, data, variables)
        self._add_ratio_constraints(gp, model, data, variables)
        self._set_weighted_objective(gp, grb, model, data, variables)
        model.update()
        return variables

    @staticmethod
    def _add_assignment_constraints(
        gp: Any,
        model: Any,
        data: ExactModelData,
        variables: _ModelVariables,
    ) -> None:
        type_indices = range(len(data.vehicle_types))
        positions = range(data.position_count)
        model.addConstrs(
            (
                gp.quicksum(variables.x[type_index, position] for type_index in type_indices)
                == 1
                for position in positions
            ),
            name="one_type_per_position",
        )
        for item in data.vehicle_types:
            model.addConstr(
                gp.quicksum(variables.x[item.index, position] for position in positions)
                == item.count,
                name=f"type_count_{item.index}",
            )

    @staticmethod
    def _add_paint_constraints(
        gp: Any,
        model: Any,
        data: ExactModelData,
        variables: _ModelVariables,
    ) -> None:
        positions = range(data.position_count)
        for color in data.colors:
            type_indices = data.type_indices_by_color[color]
            model.addConstrs(
                (
                    variables.paint_color[color, position]
                    == gp.quicksum(variables.x[type_index, position] for type_index in type_indices)
                    for position in positions
                ),
                name=f"color_link_{color}",
            )

        if data.previous_day_last_color in data.colors:
            model.addConstr(
                variables.paint_change[0]
                >= 1 - variables.paint_color[data.previous_day_last_color, 0],
                name="paint_change_day_boundary",
            )
        elif data.previous_day_last_color is not None:
            model.addConstr(variables.paint_change[0] == 1, name="forced_day_boundary_change")
        else:
            model.addConstr(variables.paint_change[0] == 0, name="no_previous_day_change")

        for position in range(1, data.position_count):
            for color in data.colors:
                model.addConstr(
                    variables.paint_change[position]
                    >= variables.paint_color[color, position - 1]
                    - variables.paint_color[color, position],
                    name=f"paint_change_{position}_{color}",
                )

        window_length = data.paint_batch_limit + 1
        for color in data.colors:
            for start in range(0, data.position_count - window_length + 1):
                model.addConstr(
                    gp.quicksum(
                        variables.paint_color[color, position]
                        for position in range(start, start + window_length)
                    )
                    <= data.paint_batch_limit,
                    name=f"paint_batch_{color}_{start}",
                )

    @staticmethod
    def _add_ratio_constraints(
        gp: Any,
        model: Any,
        data: ExactModelData,
        variables: _ModelVariables,
    ) -> None:
        for window_index, window in enumerate(data.ratio_windows):
            matching_types = tuple(
                item.index
                for item in data.vehicle_types
                if item.option_flags[window.constraint_index]
            )
            planning_count = gp.quicksum(
                variables.x[type_index, position]
                for position in window.planning_positions
                for type_index in matching_types
            )
            model.addConstr(
                variables.ratio_violation[window_index]
                >= window.previous_day_count + planning_count - window.allowed_count,
                name=f"ratio_{window.constraint_id}_{window.start_position}",
            )

    def _set_weighted_objective(
        self,
        gp: Any,
        grb: Any,
        model: Any,
        data: ExactModelData,
        variables: _ModelVariables,
    ) -> None:
        objective_values = {
            ObjectiveKind.PAINT_COLOR_CHANGES: gp.quicksum(
                variables.paint_change[position] for position in range(data.position_count)
            ),
            ObjectiveKind.HPRC_VIOLATIONS: gp.quicksum(
                variables.ratio_violation[index]
                for index, window in enumerate(data.ratio_windows)
                if window.priority is RatioPriority.HIGH
            ),
            ObjectiveKind.LPRC_VIOLATIONS: gp.quicksum(
                variables.ratio_violation[index]
                for index, window in enumerate(data.ratio_windows)
                if window.priority is RatioPriority.LOW
            ),
        }
        weighted = gp.quicksum(
            objective_values[kind]
            * self._score_base ** (CHECKER_OBJECTIVE_SLOTS - rank - 1)
            for rank, kind in enumerate(data.objective_order)
        )
        model.setObjective(weighted, sense=grb.MINIMIZE)

    @staticmethod
    def _set_seqrank_mip_start(
        instance: ProblemInstance,
        data: ExactModelData,
        variables: _ModelVariables,
    ) -> None:
        mapping = vehicle_to_type_index(data.vehicle_types)
        seqrank = sorted(
            instance.planning_day_vehicles,
            key=lambda vehicle: (vehicle.original_rank, vehicle.ident),
        )
        for position, vehicle in enumerate(seqrank):
            variables.x[mapping[vehicle.ident], position].Start = 1.0

    @staticmethod
    def _extract_type_sequence(
        data: ExactModelData,
        variables: _ModelVariables,
    ) -> tuple[int, ...]:
        result: list[int] = []
        for position in range(data.position_count):
            selected = max(
                range(len(data.vehicle_types)),
                key=lambda type_index: variables.x[type_index, position].X,
            )
            if variables.x[selected, position].X < 0.5:
                raise RuntimeError(f"No integral vehicle type selected at position {position}")
            result.append(selected)
        return tuple(result)

    @staticmethod
    def _collect_metrics(model: Any, grb: Any) -> ExactSolveMetrics:
        solution_count = int(model.SolCount)
        status = GurobiExactSolver._map_status(model.Status, solution_count, grb)
        objective = float(model.ObjVal) if solution_count else None
        best_bound = float(model.ObjBound) if math.isfinite(float(model.ObjBound)) else None
        raw_gap = float(model.MIPGap) if solution_count else math.inf
        mip_gap = raw_gap if math.isfinite(raw_gap) else None
        return ExactSolveMetrics(
            status=status,
            objective_value=objective,
            best_bound=best_bound,
            mip_gap=mip_gap,
            node_count=float(model.NodeCount),
            solution_count=solution_count,
            runtime_sec=float(model.Runtime),
        )

    @staticmethod
    def _map_status(status: int, solution_count: int, grb: Any) -> ExactSolveStatus:
        if status == grb.OPTIMAL:
            return ExactSolveStatus.OPTIMAL
        if status == grb.INFEASIBLE:
            return ExactSolveStatus.INFEASIBLE
        if status == grb.UNBOUNDED:
            return ExactSolveStatus.UNBOUNDED
        if solution_count:
            return ExactSolveStatus.FEASIBLE
        if status in {grb.INTERRUPTED, grb.USER_OBJ_LIMIT}:
            return ExactSolveStatus.INTERRUPTED
        return ExactSolveStatus.NO_SOLUTION

    @staticmethod
    def _metrics_metadata(metrics: ExactSolveMetrics) -> dict[str, object]:
        return {
            "exact_status": metrics.status.value,
            "model_objective": metrics.objective_value,
            "best_bound": metrics.best_bound,
            "mip_gap": metrics.mip_gap,
            "node_count": metrics.node_count,
            "solution_count": metrics.solution_count,
        }

    @staticmethod
    def _validate_extracted_solution(evaluation: Any, metrics: ExactSolveMetrics) -> None:
        if not evaluation.is_feasible:
            raise RuntimeError(
                "Gurobi incumbent failed independent evaluation: "
                f"{evaluation.validation_errors or 'paint batch violation'}"
            )
        if metrics.objective_value is None or evaluation.official_score is None:
            return
        if round(metrics.objective_value) != evaluation.official_score:
            raise RuntimeError(
                "MILP objective does not match independent Evaluator: "
                f"model={metrics.objective_value}, evaluator={evaluation.official_score}"
            )
