# 新一代网络建设组合决策引擎

比较多年度网络建设组合及其覆盖与成本影响。面向集团投资委员会在移动增强、
万兆光网、卫星补盲、算力节点等候选工程之间安排多年资金的场景。

## 能力概览

- **候选工程台账**：保存各工程的阶段成本、覆盖收益、产业带动、技术成熟度与退出损失。
- **组合方案生成**：在年度预算、最低普惠覆盖、区域上限、前置依赖与互斥约束下，
  枚举求解并输出稳定排序的可比较方案；净值相同的并列解名次相同、顺序确定；
  每个入选工程给出逐项入选理由，未入选工程给出说明。
- **共享收益去重**：组内工程重复计算的收益在组合时只计一次，避免重复投入。
- **敏感性分析**：需求下修、成本超支等情景（全局或单工程系数）下重估各方案，
  并在情景下重新寻优。
- **分期承诺**：委员会可把部分阶段承诺为不可撤销，承诺跨假设版本持续有效；
  已承诺但未全部建成的工程计入退出损失。
- **假设版本与审计**：假设数据只增不改，更正通过派生新版本完成；任一输出数字
  都可还原到所依据的假设版本（及情景）并重算校验。

## 模块结构

- `network_portfolio/domain`：领域模型、前置依赖图（循环检测）、指标评分、
  确定性枚举求解器、情景定义、入选理由生成。
- `network_portfolio/application`：用例服务（假设版本、承诺、方案、敏感性、审计）
  与可替换端口（时钟、标识生成、仓储）。
- `network_portfolio/infrastructure`：内存版仓储与固定时钟、顺序标识适配器。
- `network_portfolio/interfaces`：批处理接口 `BatchAPI`，字典进出，单任务失败不中断批次。

## 工程约定

项目采用 Python 包目录组织服务端代码。领域模型、应用服务、持久化适配和接口层应保持边界清晰；时间、标识生成及外部观测均通过可替换端口接入，便于稳定复现业务过程。运行数据不得写入源码目录，临时文件和本地配置由 `.gitignore` 排除。

## 使用示例

```python
from network_portfolio.infrastructure.memory import build_service
from network_portfolio.interfaces.batch import BatchAPI

api = BatchAPI(build_service())
api.handle("create_assumptions", payload)            # 候选工程 + 约束 + 权重
api.handle("commit_stages", {                        # 承诺移动增强一期不可撤销
    "assumption_version": 1, "project_id": "MOB-ENH", "stage_names": ["一期"],
})
batch = api.handle("generate_plans", {"assumption_version": 1, "max_plans": 5})
api.handle("run_sensitivity", {                      # 成本超支情景
    "batch_id": batch["data"]["batch_id"],
    "scenario": {"name": "成本超支", "cost_factor": "1.5"},
})
api.handle("audit", {                                # 还原任一数字的数据版本
    "batch_id": batch["data"]["batch_id"],
    "plan_id": batch["data"]["plans"][0]["plan_id"],
    "metric": "net_value",
})
```

## 测试

在项目根目录执行：

```bash
python3 -m unittest discover -s tests -v
```

## 编译检查

在项目根目录执行：

```bash
python3 -m compileall -q network_portfolio tests
```
