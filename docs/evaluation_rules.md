<!-- 评估规则文档：后续逐条记录并验证 paint、ratio、跨日边界和 Score 公式。 -->

# Evaluation rules

## Solution validity

- 候选解必须且只能包含全部 D 日车辆，每辆恰好出现一次。
- D-1 车辆是固定上下文，不得出现在候选解中。
- 连续同色批次不得超过 `paint_batch_limit`；HPRC/LPRC 违反是软目标。

## Paint

- 统计 D-1 最后一辆到 D 第一辆的颜色切换。
- 统计 D 内相邻车辆的颜色切换，不计算 D 最后一辆之后的切换。
- 颜色批次长度只按 D 日候选序列分段。

## Ratio constraints

- 一条 `N/P` 约束的单窗口违反量为 `max(0, observed - N)`。
- 使用最多 `P-1` 辆 D-1 尾部车辆形成跨日窗口。
- D 日尾部没有 D+1 数据，仍继续评估逐渐缩短的窗口，允许量保持为 `N`。
- 所有高优先级约束汇总为 HPRC，低优先级约束汇总为 LPRC。

## Multiple objectives and score

- 字典序目标向量严格遵循 `optimization_objectives.txt` 中的 rank。
- 当前随包 Checker 使用三个固定目标槽位和 base `100` 位权：`10000 / 100 / 1`。
- 即使 instance 只有两个目标，也使用前两个槽位 `10000 / 100`，不会缩为 `100 / 1`。
- 位权通过 Evaluator 参数隔离，后续可对齐其他 Checker 版本而不改目标计算。

上述规则已使用官方 `064_38_2_EP_RAF_ENP_ch2` 候选解对齐：内部结果与报告均为
`HPRC=0, Paint=132, LPRC=99, Score=13299`。
