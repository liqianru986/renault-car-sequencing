<!-- 数据字典：对齐官方字段、领域属性、类型、约束与匿名化业务含义。 -->

# Data dictionary

| 官方字段 | 领域属性 | 含义 |
|---|---|---|
| `Date` | `Vehicle.production_date` | 工业生产日 |
| `SeqRank` | `Vehicle.original_rank` | Renault 原工业序列名次 |
| `Ident` | `Vehicle.ident` | 车辆唯一标识符 |
| `Paint Color` | `Vehicle.paint_color` | 匿名颜色代码 |
| `HPRCi/LPRCi` | `Vehicle.option_flags[i]` | 匿名装配特征标志 |
| `Ratio` | `numerator/denominator` | 任意 P 辆中最多 N 辆关联车辆 |
| `Prio` | `RatioPriority` | 1=HPRC，0=LPRC |

