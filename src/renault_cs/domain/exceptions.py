"""领域异常：区分输入格式、领域不变式、解合法性和外部 Checker 错误。"""


class RenaultCsError(Exception):
    """项目所有可预期业务异常的基类。"""


class InstanceFormatError(RenaultCsError):
    """官方 instance 文件缺失、列结构错误或字段无法解析。"""


class DomainValidationError(RenaultCsError, ValueError):
    """已解析数据违反领域不变式。"""


class InvalidSolutionError(RenaultCsError):
    """序列存在漏车、重复、未知车辆或其他结构错误。"""


class CheckerExecutionError(RenaultCsError):
    """官方 Checker 无法启动、超时或输出不可解析。"""


class SolverUnavailableError(RenaultCsError):
    """可选求解器未安装、许可无效或当前环境不可用。"""

