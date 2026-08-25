# Renault Car Sequencing Optimization

面向汽车混流生产的可复现排产优化工程，基于 **ROADEF 2005 Renault Car Sequencing** 公开工业实例，实现官方数据解析、业务建模、MILP/ALNS 求解、增量局部搜索、独立评估与官方 Checker 验证的完整链路。

项目另提供与原赛题隔离的动态滚动重排扩展，用于模拟紧急订单、选装工位临时降产和冻结窗口，并以移动车辆数与位置偏移量化计划稳定性。

## 项目亮点

- **完整工程闭环**：Parser → Domain Model → Solver → Evaluator → Solution Writer → Official Checker → Benchmark Report。
- **两类求解方法**：车型聚合 Gurobi MILP，以及目标感知 Greedy + ALNS + 增量 VFLS。
- **精确增量评估**：支持 Swap、Forward/Backward Insert 和 Reflection，仅更新受影响的 N/P 窗口、颜色边与同色批次。
- **可复现实验**：Set A 16 个、Set X 19 个，共 35 个官方实例，统一 60 秒并逐解调用官方 Checker。
- **动态生产扩展**：统一评估紧急订单、分时段能力、冻结窗口、原赛题质量与计划扰动。

## 1. 业务问题

同一条汽车生产线混流生产不同颜色和选装配置的车辆，需要协调两个相互冲突的车间目标：

1. **装配负荷平滑**：每个高负荷选装件设置 `N/P` 滑动窗口。例如 `3/7` 表示任意连续 7 辆中，安装该选装件的车辆不应超过 3 辆。约束分为 HPRC 和 LPRC，超出数量作为软约束违法累计。
2. **喷涂批次控制**：相同颜色连续生产可减少换色清洗，但连续同色车辆不能超过 `PaintBatchLimit`，该上限为硬约束。

生产日 D 的开头还受到 D-1 尾部序列影响，因此颜色切换和 N/P 窗口均需处理跨日衔接。实例按照以下一种顺序进行字典序优化：

- `HPRC → Paint → LPRC`
- `HPRC → LPRC → Paint`
- `Paint → HPRC → LPRC`

## 2. 系统架构

```text
Official Instance Files
        │
        ▼
   RoadefParser ───────► ProblemInstance
                              │
                 ┌────────────┼────────────┐
                 ▼            ▼            ▼
             SeqRank     Gurobi MILP   Greedy + ALNS
                                             │
                                             ▼
                                      Incremental VFLS
                 └────────────┬────────────┘
                              ▼
                       SequenceSolution
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
          RenaultEvaluator          Solution Writer
                 │                         │
                 └────────────┬────────────┘
                              ▼
                    Official exeCarSeq Checker
                              │
                              ▼
                    CSV / JSON / Anytime Trace
```

```text
src/renault_cs/
├─ domain/             # 车辆、约束、目标、解与评估数据结构
├─ infrastructure/     # 官方文件、解输出、Checker与结果存储
├─ evaluation/         # Paint、N/P、评分及增量评估
├─ algorithms/         # SeqRank、Greedy、ALNS与VFLS
├─ exact/              # 车型聚合、MILP、Warm Start与解还原
├─ application/        # 单实例求解与配置编排
└─ benchmark/          # 批量实验与汇总
```

领域层不依赖 Gurobi、文件格式或具体算法；所有求解器共享同一个独立 Evaluator。

## 3. 求解方法

### 3.1 目标感知 Greedy

SeqRank 是官方原始排序字段，仅作为 Benchmark。Greedy 按 `(paint_color, option_flags)` 聚合车型，逐位置计算新增 HPRC/LPRC 违法、颜色切换和剩余选装车辆的动态紧迫度，再按实例目标顺序选择候选。ALNS 和 Gurobi Warm Start 会在硬可行的 SeqRank 与 Greedy 中选择目标向量更优者。

### 3.2 车型聚合 Gurobi MILP

静态模型将颜色和完整 `option_flags` 相同的车辆聚合为 `VehicleType`，以 `x[type, position]` 表示车型—位置分配。模型包含：

- 位置唯一分配与车型需求守恒；
- 车型—颜色联动、换色变量和同色批次硬约束；
- 包含 D-1 参数的 HPRC/LPRC 滑动窗口；
- 非负违法变量与官方多目标位权；
- 硬可行最优 Warm Start；
- Incumbent、Best Bound、MIP Gap、节点数与求解状态记录。

数学模型见 [MILP formulation](docs/milp_formulation.md)。

### 3.3 ALNS + VFLS

ALNS 包含：

- **Destroy**：Random、Violation-focused、Same-color；
- **Repair**：Greedy Insert、Regret-2 Insert；
- **Acceptance**：模拟退火接受；
- **Adaptation**：按照全局最好解、当前改善和接受解更新算子权重；
- **Intensification**：Ratio-focused Swap、Paint Relocate 与周期性 VFLS。

增量 VFLS 支持任意 Swap、双向 Insert 和区间 Reflection。状态对象维护约束窗口负荷、违法总数、颜色切换和超长批次数；区间移动通过 option 差量前缀和更新相交窗口，不对每个候选完整扫描序列。200 次随机混合移动测试中，增量结果与完整 Evaluator 逐步一致。

## 4. 60 秒官方实例实验

| 配置项 | 设置 |
|---|---:|
| 数据集 | Set A 16 个 + Set X 19 个 |
| 实例总数 | 35 |
| Gurobi / ALNS 时限 | 60 秒/实例 |
| 随机种子 | 42 |
| Gurobi MIP Gap 目标 | 1% |
| VFLS | 100 次尝试，每 10 个 ALNS 迭代触发 |
| 验证 | 官方 Windows Checker |

| 数据集 | 实例数 | ALNS 胜 | Gurobi 胜 | 同分 | ALNS 相对 SeqRank 中位改善 | Gurobi 相对 SeqRank 中位改善 |
|---|---:|---:|---:|---:|---:|---:|
| Set A | 16 | 16 | 0 | 0 | 21.26% | 1.65% |
| Set X | 19 | 17 | 0 | 2 | 19.86% | 16.23% |
| **合计** | **35** | **33** | **0** | **2** | — | — |

- 35/35 个输出均通过官方 Checker；
- Gurobi 在 2 个实例上证明最优，其余 33 个状态为 `feasible`；
- 表中比较的是相同 60 秒预算下的最好可行解，不表示 ALNS 优于 Gurobi 全局最优解；
- 胜负与相对改善均在同一实例内计算，不跨不同目标顺序直接比较绝对分数。

```powershell
.venv\Scripts\python.exe run_all_instances.py
.venv\Scripts\python.exe analyze_results.py
```

`outputs/` 默认不纳入 Git，实验产物可由脚本重新生成。

## 5. 动态滚动重排

动态事件通过独立 JSON 描述，不修改静态赛题接口。当前案例模拟：

- 新增 3 辆紧急订单；
- 前 40 位已执行，额外冻结 20 辆；
- `HPRC3` 在位置 80–150 临时调整为 `1/5`；
- 联合考虑临时能力、原赛题质量、移动车辆数与位置偏移。

动态 ALNS 仅在冻结窗口后执行 Destroy/Repair，并增加 Capacity-focused Destroy；动态 Gurobi 使用车辆级变量，以准确表达具体订单偏移。

| 指标 | 插单后未重排 | 动态 ALNS | 动态 Gurobi |
|---|---:|---:|---:|
| 临时产能违法 | 1 | 0 | 0 |
| 原赛题口径分 | 44269 | 15266 | 4272 |
| 移动车辆数 | 0 | 33 | 3 |
| 总位置偏移 | 0 | 622 | 8 |
| 平均位置偏移 | 0 | 1.86 | 0.02 |
| 最大位置偏移 | 0 | 157 | 6 |
| 冻结前缀有效 | — | 是 | 是 |

该结果来自公开实例上的模拟事件，不代表真实工厂部署。动态扩展改变了车辆集合和能力规则，因此使用扩展 Evaluator，而不调用原赛题 Checker。

```powershell
.venv\Scripts\python.exe run_dynamic_case.py
```

场景配置：[set_a_emergency_capacity_case_01.json](cases/set_a_emergency_capacity_case_01.json)。

## 6. 环境与数据

需要 Python 3.10+：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,gurobi]"
```

Gurobi 求解需要有效许可证；ALNS、Parser 和 Evaluator 不依赖商业求解器。

官方实例与 Checker 不随仓库分发，建议目录结构：

```text
parent-directory/
├─ Instances_set_A/Instances/
├─ Instances_set_X/Instances_set_X/
├─ checkers/WINDOWS/exeCarSeq.exe
└─ renault-car-sequencing/
```

数据来源：[ROADEF 2005 Challenge](https://roadef.org/challenge/2005/en/sujet.php)。

## 7. 快速开始

单实例 Gurobi 全流程：

```powershell
.venv\Scripts\python.exe main.py --time-limit 60 --mip-gap 0.01
```

CLI 运行 ALNS：

```powershell
renault-cs solve `
  --instance "<instance目录>" `
  --output "outputs/solution.txt" `
  --algorithm alns `
  --time-limit 60 `
  --seed 42 `
  --checker "<exeCarSeq.exe>"
```

支持 `seqrank`、`alns` 和 `gurobi_exact`。评估与检查已有解：

```powershell
renault-cs evaluate --instance "<instance目录>" --solution "<解文件>"
renault-cs check --instance "<instance目录>" --solution "<解文件>" --checker "<exeCarSeq.exe>"
```

## 8. 测试

```powershell
python -m pytest
```

测试覆盖 Parser、评分、Paint、N/P、解集合校验、聚合模型数据和增量移动一致性。官方 Checker 为可选集成依赖。

## 9. 文档

- [MILP 数学模型](docs/milp_formulation.md)
- [系统架构](docs/architecture.md)
- [数据字典](docs/data_dictionary.md)
- [评估规则](docs/evaluation_rules.md)

## 10. 结果边界

- 全量实验为固定种子 42 的同限时基线；
- Gurobi 未在 33 个实例上完成最优性证明，只比较限时可行解；
- 动态事件是基于公开实例生成的案例，不描述为真实工厂上线；
- 仓库不包含官方数据、Checker、Gurobi 许可证、虚拟环境和运行产物。
