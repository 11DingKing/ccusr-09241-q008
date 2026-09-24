"""领域模型：候选工程、阶段、版本化假设与约束。

领域层不依赖任何框架或持久化细节。所有金额采用同一货币单位（百万元），
年份为整数相对年（0、1、2……），由调用方解释其日历含义。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable


def stable_digest(payload: object) -> str:
    """对任意可 JSON 化对象计算稳定的 SHA-256 摘要（十六进制前 16 位）。

    排序键保证字典顺序与重复键不会影响结果，使同一数据版本永远得到同一摘要。
    """

    blob = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Phase:
    """工程的一个建设阶段。

    - ``year``：资金发生年（整数，从 0 起）。
    - ``cost``：当年投入；为简化模型，覆盖与产业带动在阶段投运年确认。
    - ``coverage``：当年新增普惠覆盖（覆盖单位，如万户）。
    - ``industrial``：当年产业带动（与金额同单位，可为负外部性之外的正向值）。
    - ``exit_loss``：若工程启动后最终未完成全部阶段，已沉没投入的退出损失系数基数，
      实际退出损失在引擎中按已承诺阶段成本汇总（此处保留阶段粒度供审计）。
    """

    year: int
    cost: float
    coverage: float = 0.0
    industrial: float = 0.0
    exit_loss: float = 0.0

    def validate(self) -> None:
        if not isinstance(self.year, int) or self.year < 0:
            raise ValueError(f"阶段年份必须为非负整数，得到 {self.year!r}")
        if self.cost < 0:
            raise ValueError("阶段成本不能为负")
        if self.coverage < 0 or self.industrial < 0 or self.exit_loss < 0:
            raise ValueError("覆盖、产业带动与退出损失不能为负")


@dataclass(frozen=True)
class Project:
    """候选工程：跨年度的多阶段建设对象（移动增强/万兆光网/卫星补盲/算力节点等）。"""

    code: str
    name: str
    category: str
    region: str
    phases: tuple[Phase, ...]
    # 成熟度 0.0~1.0，作为收益的确定性折扣与入选理由的一部分
    maturity: float = 1.0
    # 前置工程：本工程任一阶段投运前，depends_on 工程必须至少完成其首阶段
    depends_on: frozenset[str] = frozenset()
    # 互斥工程：同一组合中不得同时出现（如重复覆盖的两套技术路线）
    excludes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.code or not isinstance(self.code, str):
            raise ValueError("工程编码不能为空")
        if not (0.0 <= self.maturity <= 1.0):
            raise ValueError("技术成熟度必须落在 [0, 1]")
        years = [p.year for p in self.phases]
        if years != sorted(years):
            raise ValueError(f"工程 {self.code} 的阶段必须按年份升序排列")
        if len(set(years)) != len(years):
            raise ValueError(f"工程 {self.code} 同一年份存在多个阶段")
        for phase in self.phases:
            phase.validate()

    @property
    def years(self) -> tuple[int, ...]:
        return tuple(p.year for p in self.phases)

    @property
    def total_cost(self) -> float:
        return sum(p.cost for p in self.phases)

    @property
    def total_coverage(self) -> float:
        return sum(p.coverage for p in self.phases)

    @property
    def total_industrial(self) -> float:
        return sum(p.industrial for p in self.phases)

    def phases_through(self, year: int) -> tuple[Phase, ...]:
        return tuple(p for p in self.phases if p.year <= year)


@dataclass(frozen=True)
class Constraints:
    """年度组合约束。

    - ``annual_budget``：年份 -> 当年资金上限。
    - ``min_annual_coverage``：年份 -> 截至该年累计普惠覆盖下限。
    - ``region_cap``：区域 -> 组合内该区域工程数量上限。
    - ``category_cap``：类别 -> 组合内该类别工程数量上限（可选）。
    """

    annual_budget: dict[int, float]
    min_annual_coverage: dict[int, float] = field(default_factory=dict)
    region_cap: dict[str, int] = field(default_factory=dict)
    category_cap: dict[str, int] = field(default_factory=dict)

    def validate(self, horizon: int) -> None:
        for year, budget in self.annual_budget.items():
            if not 0 <= year < horizon:
                raise ValueError(f"预算年份 {year} 超出规划期 [0, {horizon})")
            if budget < 0:
                raise ValueError("年度预算不能为负")
        for year, floor in self.min_annual_coverage.items():
            if not 0 <= year < horizon or floor < 0:
                raise ValueError("普惠覆盖下限的年份或取值非法")
        for cap in self.region_cap.values():
            if cap < 0:
                raise ValueError("区域上限不能为负")


@dataclass(frozen=True)
class Scenario:
    """敏感性情景：对成本与需求（覆盖收益）的整体冲击。

    - ``cost_overrun``：成本乘数偏移，0.15 表示全部阶段成本上浮 15%。
    - ``demand_shock``：覆盖乘数偏移，-0.2 表示覆盖下修 20%（产业带动同步下修）。
    """

    code: str
    name: str
    cost_overrun: float = 0.0
    demand_shock: float = 0.0

    def apply_cost(self, cost: float) -> float:
        return cost * (1.0 + self.cost_overrun)

    def apply_coverage(self, value: float) -> float:
        return value * (1.0 + self.demand_shock)


@dataclass(frozen=True)
class AssumptionSet:
    """一版不可变假设：项目全集 + 约束 + 规划期 + 权重。

    对象一经创建即冻结，派生新版时必须构造新对象并取得新摘要；
    任何已被方案引用的历史版本都不会被改写。
    """

    label: str
    projects: tuple[Project, ...]
    constraints: Constraints
    horizon: int
    # 目标加权：净收益（产业带动折算后）与覆盖在统一打分中的权重
    weight_industrial: float = 1.0
    weight_coverage: float = 1.0
    discount_rate: float = 0.0
    scenarios: tuple[Scenario, ...] = ()

    def __post_init__(self) -> None:
        codes = [p.code for p in self.projects]
        if len(set(codes)) != len(codes):
            raise ValueError("假设集中工程编码重复")
        if self.horizon <= 0:
            raise ValueError("规划期年数必须为正整数")
        for project in self.projects:
            bad = [d for d in project.depends_on if d not in set(codes)]
            if bad:
                raise ValueError(f"工程 {project.code} 依赖不存在的工程 {sorted(bad)}")
            bad = [x for x in project.excludes if x not in set(codes)]
            if bad:
                raise ValueError(f"工程 {project.code} 互斥指向不存在的工程 {sorted(bad)}")
            if any(p.year >= self.horizon for p in project.phases):
                raise ValueError(f"工程 {project.code} 存在超出规划期的阶段")
        self.constraints.validate(self.horizon)
        self._detect_cycles()

    def _detect_cycles(self) -> None:
        deps = {p.code: set(p.depends_on) for p in self.projects}
        # 互斥不是前置关系，不参与环检测；前置必须无环
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(node: str, stack: tuple[str, ...]) -> None:
            if node in done:
                return
            if node in visiting:
                cycle = list(stack[stack.index(node):]) + [node]
                raise CyclicDependencyError(cycle)
            visiting.add(node)
            for nxt in sorted(deps.get(node, ())):
                visit(nxt, stack + (node,))
            visiting.discard(node)
            done.add(node)

        for code in sorted(deps):
            visit(code, ())

    @property
    def project_map(self) -> dict[str, Project]:
        return {p.code: p for p in self.projects}

    def digest_payload(self) -> dict[str, object]:
        return {
            "label": self.label,
            "horizon": self.horizon,
            "weight_industrial": self.weight_industrial,
            "weight_coverage": self.weight_coverage,
            "discount_rate": self.discount_rate,
            "projects": [
                {
                    "code": p.code,
                    "name": p.name,
                    "category": p.category,
                    "region": p.region,
                    "maturity": p.maturity,
                    "depends_on": sorted(p.depends_on),
                    "excludes": sorted(p.excludes),
                    "phases": [
                        {
                            "year": ph.year,
                            "cost": ph.cost,
                            "coverage": ph.coverage,
                            "industrial": ph.industrial,
                            "exit_loss": ph.exit_loss,
                        }
                        for ph in p.phases
                    ],
                }
                for p in sorted(self.projects, key=lambda x: x.code)
            ],
            "constraints": {
                "annual_budget": {str(k): v for k, v in
                                  sorted(self.constraints.annual_budget.items())},
                "min_annual_coverage": {str(k): v for k, v in
                                        sorted(self.constraints.min_annual_coverage.items())},
                "region_cap": dict(sorted(self.constraints.region_cap.items())),
                "category_cap": dict(sorted(self.constraints.category_cap.items())),
            },
            "scenarios": [
                {"code": s.code, "name": s.name,
                 "cost_overrun": s.cost_overrun, "demand_shock": s.demand_shock}
                for s in sorted(self.scenarios, key=lambda x: x.code)
            ],
        }

    def digest(self) -> str:
        return stable_digest(self.digest_payload())

    def derive(self, *, label: str | None = None,
               projects: Iterable[Project] | None = None,
               constraints: Constraints | None = None,
               horizon: int | None = None,
               weight_industrial: float | None = None,
               weight_coverage: float | None = None,
               discount_rate: float | None = None,
               scenarios: Iterable[Scenario] | None = None) -> "AssumptionSet":
        """基于当前版本派生新版（当前版本保持不变）。"""

        return AssumptionSet(
            label=label if label is not None else self.label,
            projects=tuple(projects) if projects is not None else self.projects,
            constraints=constraints or self.constraints,
            horizon=horizon if horizon is not None else self.horizon,
            weight_industrial=(weight_industrial if weight_industrial is not None
                               else self.weight_industrial),
            weight_coverage=(weight_coverage if weight_coverage is not None
                             else self.weight_coverage),
            discount_rate=discount_rate if discount_rate is not None else self.discount_rate,
            scenarios=tuple(scenarios) if scenarios is not None else self.scenarios,
        )


class CyclicDependencyError(ValueError):
    """前置依赖图存在环。"""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__("检测到循环依赖: " + " -> ".join(cycle))
