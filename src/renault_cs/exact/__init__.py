"""精确求解适配层：导出公共指标、聚合数据与可选 Gurobi 后端。"""

from renault_cs.exact.base import ExactSolveMetrics, ExactSolveStatus
from renault_cs.exact.gurobi_model import GurobiExactSolver
from renault_cs.exact.model_data import ExactModelData, RatioWindow, build_exact_model_data
from renault_cs.exact.vehicle_types import VehicleType, aggregate_vehicle_types

__all__ = [
    "ExactModelData",
    "ExactSolveMetrics",
    "ExactSolveStatus",
    "GurobiExactSolver",
    "RatioWindow",
    "VehicleType",
    "aggregate_vehicle_types",
    "build_exact_model_data",
]
