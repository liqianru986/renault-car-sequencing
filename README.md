# Renault Car Sequencing

ROADEF 2005 Renault Car Sequencing 的可复现求解与评测工程。项目覆盖目标感知 Greedy、
聚合车辆类型的 Gurobi MILP、ALNS + 增量 VFLS、内部 Evaluator、官方格式 I/O、Windows
Checker，以及紧急订单、临时降产和冻结窗口下的动态滚动重排。

## 第一次运行（推荐）

在项目根目录双击打开终端，执行：

```powershell
.venv\Scripts\python.exe main.py
```

`main.py` 默认运行 Set X 的小实例 `028_CH2_EP_ENP_RAF_S51_J1`，控制台按六个阶段展示：

1. 读取 instance；
2. 构造车辆类型与滑动窗口；
3. 构建并求解 Gurobi MILP；
4. 独立 Evaluator 复评；
5. 写出官方格式解；
6. 官方 Checker 对齐。

结果位于 `outputs/runs/028_CH2_EP_ENP_RAF_S51_J1/`。其中 `application.log` 是业务流程日志，
`gurobi.log` 是求解过程，`model.lp` 可直接查看模型，`solution.txt` 是正式解，
`run_summary.json` 汇总本次实验指标。

如需改变运行限制：

```powershell
.venv\Scripts\python.exe main.py --time-limit 60 --mip-gap 0.01
```

## 项目边界

- 输入：ROADEF 2005 官方 instance 目录。
- 决策：对生产日 D 的车辆进行排列。
- 硬约束：车辆完整性、D-1 固定、最大颜色批次。
- 软约束：HPRC/LPRC 滑动窗口违反。
- 目标：按 instance 声明的优先级优化 Paint、HPRC 和 LPRC。
- 验证：自研 Evaluator 与官方 Checker 对齐。

## 环境安装

需要 Python 3.10+：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

安装后使用统一命令 `renault-cs`。所有路径均可指向项目外的官方数据目录。

使用 Gurobi Exact 需在同一 Python 3.10+ 环境中额外安装：

```powershell
python -m pip install -e ".[gurobi]"
```

## 快速运行

查看实例：

```powershell
renault-cs inspect --instance "<instance目录>"
```

运行 SeqRank 并同时调用官方 Checker：

```powershell
renault-cs solve `
  --instance "<instance目录>" `
  --output "outputs/solutions/solution.txt" `
  --algorithm seqrank `
  --checker "<checkers/WINDOWS/exeCarSeq.exe>"
```

评估已有解：

```powershell
renault-cs evaluate --instance "<instance目录>" --solution "<解文件>"
```

对齐内部结果与官方 Checker：

```powershell
renault-cs check `
  --instance "<instance目录>" `
  --solution "<解文件>" `
  --checker "<exeCarSeq.exe>"
```

批量运行数据集：

```powershell
renault-cs benchmark `
  --dataset "<包含多个instance的目录>" `
  --output-dir "outputs/benchmarks/set_a" `
  --algorithm seqrank `
  --time-limit 60 `
  --seeds 42 `
  --checker "<exeCarSeq.exe>"
```

批量产物包括：

- `benchmark_records.csv`：每次运行的原子指标；
- `benchmark_summary.json`：算法级中位数、可行率与 Checker 通过率；
- `solutions/`：官方格式解；
- `checker_reports/`：官方原始报告。

运行聚合 MILP：

```powershell
renault-cs solve `
  --instance "<instance目录>" `
  --output "outputs/solutions/gurobi_solution.txt" `
  --algorithm gurobi_exact `
  --time-limit 300 `
  --mip-gap 0.01 `
  --threads 0 `
  --checker "<checkers/WINDOWS/exeCarSeq.exe>"
```

Exact 会在硬可行的 SeqRank 与 Greedy 中选择目标向量更优者作为 MIP Start，并返回
Incumbent、Best Bound、MIP Gap、节点数和运行时间。
使用 `--no-mip-start` 可关闭初始解，使用 `--quiet-solver` 可关闭 Gurobi 控制台日志。

运行完整 ALNS（Destroy、Repair、模拟退火接受和自适应权重更新）：

```powershell
.venv\Scripts\python.exe -m renault_cs.cli solve `
  --instance "<instance目录>" `
  --output "outputs/alns_solution.txt" `
  --algorithm alns `
  --time-limit 60 `
  --seed 42 `
  --checker "<checkers/WINDOWS/exeCarSeq.exe>"
```

当前 Destroy 包含 Random、Violation-focused 和 Same-color，Repair 包含 Greedy 与
Regret-2。局部强化实现增量 Swap、Forward/Backward Insert 与 Reflection；比例窗口、
颜色切换和同色批次均局部更新。运行结果会输出算子权重、贡献次数和 anytime 收敛轨迹。

## 动态滚动重排案例

直接运行根目录的 `run_dynamic_case.py`。它会读取
`cases/set_a_emergency_capacity_case_01.json`，加入紧急订单，固定已执行区与冻结窗口，并在指定
位置区间把选装工位的 N/P 负荷要求临时收紧后滚动重排剩余序列。输出包括动态产能违法、
原赛题目标口径、移动订单数和位置偏移。新增车辆与动态约束不属于原始赛题，因此使用扩展
Evaluator验证，不调用只认识原始实例的官方Checker。动态 ALNS 与车辆级动态 Gurobi 使用
同一组指标：临时产能违法、原赛题指标、移动车辆数、总/平均/最大位置偏移和冻结前缀有效性。

```powershell
.venv\Scripts\python.exe run_dynamic_case.py
```

## 60秒全量实验

正式实验包含 Set A 16个与 Set X 19个，共35个官方实例。Gurobi 与 ALNS 均限制60秒，
固定随机种子42，所有输出均通过官方 Checker。

| 数据集 | 实例数 | ALNS胜 | Gurobi胜 | 同分 | ALNS相对SeqRank中位改善 | Gurobi相对SeqRank中位改善 |
|---|---:|---:|---:|---:|---:|---:|
| Set A | 16 | 16 | 0 | 0 | 21.26% | 1.65% |
| Set X | 19 | 17 | 0 | 2 | 19.86% | 16.23% |
| 合计 | 35 | 33 | 0 | 2 | — | — |

Gurobi 状态为33个 `feasible`、2个 `optimal`，因此上述结果是同限时 Incumbent 比较，
不是“ALNS优于数学规划最优解”的证明。运行与汇总：

```powershell
.venv\Scripts\python.exe run_all_instances.py
.venv\Scripts\python.exe analyze_results.py
```

动态案例中，两种算法均将临时产能违法从1降为0并保持冻结前缀：动态ALNS移动33辆、
总偏移622；动态Gurobi移动3辆、总偏移8。该案例是基于官方实例构造的模拟事件，不代表上线数据。

详细流程、参数和面试问题见
[`docs/项目全流程与面试复习_60秒实验.md`](docs/项目全流程与面试复习_60秒实验.md)。

## 已验证基准

`instance_039_ch1_s26_mar` 的 SeqRank 结果已与随包 Checker 对齐：

| 指标 | 内部 Evaluator | 官方 Checker |
|---|---:|---:|
| Paint | 86 | 86 |
| HPRC | 28 | 28 |
| LPRC | 978 | 978 |
| Score | 863778 | 863778 |

Set A 与 Set X 共35个实例已完成 SeqRank、Gurobi、ALNS、解输出和官方 Checker 全流程。

## 依赖规则

`domain` 不依赖文件格式、Gurobi 或具体算法；所有求解器共用同一 Evaluator。

Exact 模型的集合、参数、变量、目标和约束见
[`docs/milp_formulation.md`](docs/milp_formulation.md)。

## 已完成模块

1. 工程骨架、Parser、Evaluator、Writer 与官方 Checker；
2. SeqRank Benchmark 与目标感知 Greedy；
3. 聚合 MILP、可行最优 Warm Start 与独立解还原；
4. ALNS、增量 VFLS、算子权重和收敛轨迹；
5. Set A/Set X 35实例60秒正式 Benchmark；
6. 紧急插单、临时降产、冻结窗口和计划偏移下的动态 ALNS/Gurobi。

## 测试

```powershell
python -m pytest
```

官方 Checker 为可选集成依赖，普通单元测试不要求其存在。
