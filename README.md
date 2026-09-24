# 新一代网络建设组合决策引擎

在移动增强、万兆光网、卫星补盲与算力节点等候选工程之间安排多年资金：
保存阶段成本、覆盖收益、产业带动、技术成熟度与退出损失，在年度预算、
最低普惠覆盖、区域上限及依赖/互斥关系下生成**稳定排序**的可比较方案，
给出逐项入选理由，并对需求下修、成本超支等情景输出敏感性结果。

## 分层结构

| 层 | 模块 | 职责 |
| --- | --- | --- |
| 领域 | `network_portfolio.domain` | `Project`/`Phase`/`Constraints`/`Scenario`/`AssumptionSet`，无框架依赖，含循环依赖检测与版本摘要 |
| 引擎 | `network_portfolio.engine` | 阶段前缀组合枚举、约束校验、确定性排序、逐项理由、情景重优化 |
| 应用 | `network_portfolio.application` | 版本登记/派生、不可撤销承诺、批处理服务、审计还原；端口（时钟、标识、仓库）可替换 |
| 持久化 | `network_portfolio.persistence` | `InMemoryRepository` 与追加式、原子写入的 `JsonFileRepository` |
| 接口 | `network_portfolio.interface` / `cli.py` | 纯 JSON 批处理信封、审计查询信封、命令行入口 |

## 核心规则

- **分期建模**：工程由按年排序的阶段组成；方案中每个工程以"阶段前缀 k"出现
  （选前 k 期，k=0 不入选），天然表达分期承诺；已启动而未建成的工程计退出损失。
- **约束**：逐年预算、逐年累计普惠覆盖下限、区域/类别工程数上限、
  前置（被依赖方须入选且首阶段不晚）、互斥（同方案不得共选）。
- **不可撤销承诺**：委员会可锁定某工程的最少阶段前缀；多次承诺按各工程取最大前缀合并。
- **版本不可变**：每版假设有 SHA-256 摘要与完整自描述快照；参数更正只能
  `derive_assumptions` 派生新版本，父版本原样保留，已被批处理引用的历史版本永不可改写。
- **稳定排序**：方案按 `(折现得分降序, 覆盖降序, 名义成本升序, 规范选择键升序)` 排序，
  与工程输入顺序无关；方案与版本均有稳定摘要。
- **审计可还原**：`audit` 可对方案总分、名义合计、逐年金额、单工程指标还原出
  所引用的假设版本、逐阶段原始输入（`raw_*`）、情景有效值与计算公式。

## 快速上手（Python API）

```python
from network_portfolio.application import PortfolioService, SequentialIdGenerator, FixedClock
from network_portfolio.persistence import InMemoryRepository
from network_portfolio.interface import run_planning_batch, audit_number

service = PortfolioService(InMemoryRepository(),
                           FixedClock("2026-09-24T00:00:00+00:00"),
                           SequentialIdGenerator())
version = service.register_assumptions(assumptions)
service.commit(version.version_id, {"MOBILE": 1})          # 锁定一期
env = run_planning_batch(service, version.version_id, top_k=5)
trace = audit_number(service, env["batch_id"], "totals.nominal_cost")
new_version = service.derive_assumptions(version.version_id, label="v2",
                                         constraints=tighter_constraints)
```

## 命令行批处理

数据目录必须显式指定，运行数据不会写入源码目录：

```bash
python -m network_portfolio.cli --data-dir /var/lib/np register --data assumptions.json
python -m network_portfolio.cli --data-dir /var/lib/np commit  --version version-1 --data commit.json
python -m network_portfolio.cli --data-dir /var/lib/np run     --version version-1 --top-k 5
python -m network_portfolio.cli --data-dir /var/lib/np audit   --batch batch-1 --metric score
python -m network_portfolio.cli --data-dir /var/lib/np list
```

可审计指标：`score`、`totals.nominal_cost|nominal_coverage|nominal_industrial|exit_loss`、
`annual_cost.<年>`、`annual_coverage.<年>`、
`item.<工程编码>.nominal_cost|coverage|industrial|exit_loss|score_contribution`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

覆盖：循环依赖（直接环、三节点环、自环、DAG、互斥不算环）、并列解与
输入顺序无关的稳定排序、预算/覆盖/区域/前置/互斥约束、不可行判定、
分期承诺（前缀下限、退出损失、承诺与互斥冲突）、需求下修与成本超支情景、
版本派生与历史不可变、假设更正、批处理复现以及逐数字审计还原。

## 编译检查

```bash
python3 -m compileall -q network_portfolio tests
```
