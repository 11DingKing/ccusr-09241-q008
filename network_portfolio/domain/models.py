"""领域模型：候选工程、假设版本、分期承诺、方案与审计结果。

所有金额与收益均使用 Decimal，保证排序、并列判定与审计重算完全可复现。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from .errors import ValidationError

ZERO = Decimal("0")
ONE = Decimal("1")


def to_decimal(value: object, field: str = "数值") -> Decimal:
    """把 JSON 友好的输入（int/float/str/Decimal）转换为 Decimal。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        raise ValidationError(f"{field} 不是有效数字: {value!r}")
    if isinstance(value, (int, float, str)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            raise ValidationError(f"{field} 不是有效数字: {value!r}") from None
    raise ValidationError(f"{field} 不是有效数字: {value!r}")


def fmt(value: Decimal) -> str:
    """面向报告与审计的定点格式，避免科学计数法。"""
    return format(value, "f")


@dataclass(frozen=True)
class Stage:
    """工程的一个建设阶段：成本发生年份，以及建成后的覆盖与产业收益。"""

    name: str
    year: int
    cost: Decimal
    coverage: Decimal
    industry: Decimal


@dataclass(frozen=True)
class Project:
    """候选工程。阶段按建设顺序排列，选择某一期即选择其全部前序期。"""

    project_id: str
    name: str
    category: str  # 移动增强 / 万兆光网 / 卫星补盲 / 算力节点 等
    region: str
    maturity: Decimal  # 技术成熟度 0..1，用于折算收益
    exit_loss: Decimal  # 已承诺但未全部建成时的退出损失
    stages: tuple[Stage, ...]


@dataclass(frozen=True)
class SharedBenefit:
    """共享收益：组内工程重复计算的收益部分，组合时重复部分只计一次。"""

    group_id: str
    project_ids: tuple[str, ...]
    coverage_overlap: Decimal
    industry_overlap: Decimal


@dataclass(frozen=True)
class Constraints:
    """组合层面的硬约束。"""

    annual_budget: tuple[tuple[int, Decimal], ...]  # (年份, 预算)，按年排序
    min_coverage: Decimal  # 最低普惠覆盖
    regional_caps: tuple[tuple[str, Decimal], ...]  # (区域, 总成本上限)
    prerequisites: tuple[tuple[str, str], ...]  # (依赖者, 前置工程)
    mutex_groups: tuple[tuple[str, ...], ...]  # 互斥组，组内至多入选一个

    def budget_map(self) -> dict[int, Decimal]:
        return dict(self.annual_budget)

    def region_cap_map(self) -> dict[str, Decimal]:
        return dict(self.regional_caps)


@dataclass(frozen=True)
class Weights:
    """目标函数权重：净值 = (覆盖×覆盖权重 + 产业×产业权重)×成熟度 − 成本权重×成本 − 退出损失。"""

    coverage: Decimal = ONE
    industry: Decimal = ONE
    cost: Decimal = ONE


@dataclass(frozen=True)
class AssumptionSet:
    """一版不可变的假设数据。任何更正都通过派生新版本完成，历史版本只增不改。"""

    version: int
    parent_version: Optional[int]
    label: str
    change_note: str
    projects: tuple[Project, ...]
    shared_benefits: tuple[SharedBenefit, ...]
    constraints: Constraints
    weights: Weights
    created_at: str

    def project_map(self) -> dict[str, Project]:
        return {p.project_id: p for p in self.projects}


@dataclass(frozen=True)
class Commitment:
    """委员会对若干阶段的不可撤销承诺，跨假设版本持续有效。"""

    commitment_id: str
    assumption_version: int  # 作出承诺时所依据的假设版本
    project_id: str
    stage_names: tuple[str, ...]
    decided_by: str
    decided_at: str
    note: str


@dataclass(frozen=True)
class PlanMetrics:
    """一个方案的可比较指标。"""

    total_cost: Decimal
    coverage: Decimal  # 名义普惠覆盖（共享重复已扣除）
    industry: Decimal  # 名义产业带动（共享重复已扣除）
    exit_penalty: Decimal  # 已承诺但未全部建成工程的退出损失合计
    net_value: Decimal  # 排序目标值
    cost_by_year: tuple[tuple[int, Decimal], ...]
    cost_by_region: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class PlanItem:
    """方案中的一个入选工程及其入选理由。"""

    project_id: str
    stage_names: tuple[str, ...]
    cost: Decimal
    coverage: Decimal
    industry: Decimal
    contribution: Decimal  # 边际净贡献：方案净值 − 移除该工程后的净值
    rationale: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    plan_id: str
    rank: int  # 竞争排名：净值相同者名次相同
    tied: bool  # 是否存在与本方案净值相同的并列方案
    items: tuple[PlanItem, ...]
    metrics: PlanMetrics
    notes: tuple[str, ...]
    excluded_notes: tuple[tuple[str, str], ...]  # (工程, 未入选说明)

    @property
    def selection(self) -> dict[str, int]:
        """project_id -> 已选阶段数。"""
        return {item.project_id: len(item.stage_names) for item in self.items}


@dataclass(frozen=True)
class PlanBatch:
    """一次方案生成结果，记录所依据的假设版本与承诺快照，供审计还原。"""

    batch_id: str
    assumption_version: int
    generated_at: str
    feasible: bool
    plans: tuple[Plan, ...]
    diagnostics: tuple[str, ...]
    committed_snapshot: tuple[tuple[str, tuple[str, ...]], ...]  # (工程, 已承诺阶段名)

    def committed_counts(self) -> dict[str, int]:
        return {pid: len(names) for pid, names in self.committed_snapshot}


@dataclass(frozen=True)
class PlanScenarioResult:
    """单个方案在情景下的重估结果。"""

    plan_id: str
    base_net_value: Decimal
    scenario_net_value: Decimal
    delta: Decimal
    feasible: bool
    violations: tuple[str, ...]


@dataclass(frozen=True)
class SensitivityReport:
    """一次敏感性分析：情景定义随报告保存，审计时可完整还原。"""

    report_id: str
    batch_id: str
    assumption_version: int
    scenario_name: str
    scenario: object  # domain.scenarios.Scenario，避免循环引用
    generated_at: str
    results: tuple[PlanScenarioResult, ...]
    optimal_selection: tuple[tuple[str, int], ...]  # 情景下重新寻优的结果
    optimal_net_value: Optional[Decimal]
    optimal_feasible: bool
    diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class AuditResult:
    """一个数字的溯源结果：当时记录值、所依据的数据版本与重算校验。"""

    subject: str  # "plan" 或 "sensitivity"
    source_id: str  # 批次或报告标识
    plan_id: str
    project_id: Optional[str]
    metric: str
    value: Decimal  # 当时记录的数字
    recomputed: Decimal  # 依据版本化数据重算的数字
    verified: bool  # 两者一致即为真
    assumption_version: int
    scenario_name: Optional[str]
    formula: str
    inputs: tuple[tuple[str, str], ...]  # (输入项, 取值)，按计算顺序排列
