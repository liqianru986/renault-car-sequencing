"""领域枚举：统一比例约束优先级、HPRC 难度标签和官方目标类型。"""

from enum import Enum, IntEnum


class RatioPriority(IntEnum):
    """比例约束优先级；数值与 ratios.txt 的 Prio 字段一致。"""

    LOW = 0
    HIGH = 1


class HprcDifficulty(str, Enum):
    """Renault 对整个 instance 的 HPRC 实验难度标签。"""

    EASY = "easy"
    DIFFICULT = "difficult"


class ObjectiveKind(str, Enum):
    """经过标准化的赛题目标，用于隔离官方长字符串与内部业务逻辑。"""

    PAINT_COLOR_CHANGES = "paint_color_changes"
    HPRC_VIOLATIONS = "hprc_violations"
    LPRC_VIOLATIONS = "lprc_violations"

