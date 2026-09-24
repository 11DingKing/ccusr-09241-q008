"""组合枚举与排序引擎。

每个工程在方案中以"阶段前缀"出现：选择前 k 个阶段（k=0 表示不入选），
从而自然表达分期承诺与退出。引擎在年度预算、累计普惠覆盖下限、
区域/类别上限、前置、互斥与不可撤销承诺下枚举可行组合，并按
确定性规则排序，输出逐项入选理由。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..domain import AssumptionSet, Project, stable_digest

# 枚举安全阈值：可行方案数超过该值时按确定性顺序截断并标记
_MAX_PLANS = 50_000
_EPS = 1e-9


class InfeasibleError(RuntimeError):
    """在当前约束与承诺下不存在任何可行组合。"""


@dataclass(frozen=True)
class Selection:
    """单个工程在方案中的选择：前 ``prefix`` 个阶段（prefix=0 表示不入选）。"""

    code: str
    prefix: int

    @property
    def selected(self) -> bool:
        return self.prefix > 0


@dataclass(frozen=True)
class ItemReason:
    """逐项入选理由：所有数字均可在对应假设版本中复核。"""

    code: str
    name: str
    category: str
    region: str
    prefix: int
    total_phases: int
    committed_prefix: int
    maturity: float
    coverage: float
    industrial: float
    nominal_cost: float
    score_contribution: float
    exit_loss: float
    roles: tuple[str, ...]
    rationale: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    """一个可比较的组合方案。

    ``annual_*`` 为情景调整后口径；``total_nominal_*`` 为名义口径，
    保证不同情景下的同一方案仍可对照。
    """

    plan_digest: str
    assumption_digest: str
    assumption_label: str
    selections: tuple[Selection, ...]
    rank: int
    score: float
    total_nominal_cost: float
    total_nominal_coverage: float
    total_nominal_industrial: float
    annual_cost: dict[int, float]
    annual_coverage: dict[int, float]
    exit_loss: float
    reasons: tuple[ItemReason, ...]
    commitments: dict[str, int]
    cost_overrun: float
    demand_shock: float
    truncated: bool = False

    @property
    def selected_codes(self) -> frozenset[str]:
        return frozenset(s.code for s in self.selections if s.selected)

    def canonical_key(self) -> str:
        return "|".join(f"{s.code}:{s.prefix}" for s in self.selections)


@dataclass
class _Accum:
    annual_cost: dict[int, float] = field(default_factory=dict)
    annual_coverage: dict[int, float] = field(default_factory=dict)
    score: float = 0.0
    exit_loss: float = 0.0


def _discount(rate: float, year: int) -> float:
    return 1.0 / ((1.0 + rate) ** year)


def _prefix_score(proj: Project, k: int, assumptions: AssumptionSet,
                  cost_overrun: float, demand_shock: float) -> float:
    """前 k 期的折现得分贡献（含未建成工程的退出损失罚项）。"""

    rate = assumptions.discount_rate
    w_cov = assumptions.weight_coverage
    w_ind = assumptions.weight_industrial
    score = 0.0
    for ph in proj.phases[:k]:
        df = _discount(rate, ph.year)
        score += df * proj.maturity * (
            w_cov * ph.coverage * (1.0 + demand_shock)
            + w_ind * ph.industrial * (1.0 + demand_shock)
        )
    if 0 < k < len(proj.phases):
        penalty = sum(ph.exit_loss for ph in proj.phases[:k])
        score -= _discount(rate, proj.phases[k - 1].year) * penalty
    return score


def solve(
    assumptions: AssumptionSet,
    commitments: Mapping[str, int] | None = None,
    *,
    top_k: int = 5,
    cost_overrun: float = 0.0,
    demand_shock: float = 0.0,
) -> list[Plan]:
    """在给定假设版本与不可撤销承诺下生成稳定排序后的方案集合。

    ``commitments`` 为工程编码 -> 最少必须包含的阶段前缀数。
    ``cost_overrun`` / ``demand_shock`` 供情景重优化使用，基准调用取 0。
    """

    if top_k <= 0:
        raise ValueError("top_k 必须为正整数")
    commits = dict(commitments or {})
    projects = sorted(assumptions.projects, key=lambda p: p.code)
    unknown = sorted(set(commits) - {p.code for p in projects})
    if unknown:
        raise ValueError(f"承诺指向不存在的工程: {unknown}")
    for code, k in commits.items():
        if not isinstance(k, int) or k < 0:
            raise ValueError(f"工程 {code} 的承诺前缀数必须为非负整数")
        if k > len(assumptions.project_map[code].phases):
            raise ValueError(f"工程 {code} 的承诺阶段超出其阶段总数")

    pmap = assumptions.project_map
    con = assumptions.constraints
    budget = con.annual_budget

    plans: list[Plan] = []
    truncated = False
    chosen: dict[str, int] = {}
    acc = _Accum()

    def apply(proj: Project, k: int) -> tuple[list[tuple[int, float, float]], float, float] | None:
        """把前 k 期写入累计器；预算超限返回 None 且不留副作用。"""

        deltas: list[tuple[int, float, float]] = []
        for ph in proj.phases[:k]:
            cost = ph.cost * (1.0 + cost_overrun)
            cov = ph.coverage * (1.0 + demand_shock)
            if acc.annual_cost.get(ph.year, 0.0) + cost > budget.get(ph.year, float("inf")) + _EPS:
                return None
            deltas.append((ph.year, cost, cov))
        score_delta = _prefix_score(proj, k, assumptions, cost_overrun, demand_shock)
        exit_delta = sum(ph.exit_loss for ph in proj.phases[:k]) if 0 < k < len(proj.phases) else 0.0
        for year, cost, cov in deltas:
            acc.annual_cost[year] = acc.annual_cost.get(year, 0.0) + cost
            acc.annual_coverage[year] = acc.annual_coverage.get(year, 0.0) + cov
        acc.score += score_delta
        acc.exit_loss += exit_delta
        return (deltas, score_delta, exit_delta)

    def undo(frame: tuple[list[tuple[int, float, float]], float, float]) -> None:
        deltas, score_delta, exit_delta = frame
        for year, cost, cov in deltas:
            acc.annual_cost[year] -= cost
            acc.annual_coverage[year] -= cov
        acc.score -= score_delta
        acc.exit_loss -= exit_delta

    def feasible() -> bool:
        # 前置：被依赖方入选，且其首阶段不晚于依赖方首阶段
        for proj in projects:
            k = chosen[proj.code]
            if k == 0:
                continue
            first_year = proj.phases[0].year
            for dep_code in proj.depends_on:
                if chosen[dep_code] == 0:
                    return False
                if pmap[dep_code].phases[0].year > first_year:
                    return False
        # 互斥
        for proj in projects:
            if chosen[proj.code] and any(chosen[x] > 0 for x in proj.excludes):
                return False
        # 区域 / 类别上限
        region_count: dict[str, int] = {}
        category_count: dict[str, int] = {}
        for proj in projects:
            if chosen[proj.code]:
                region_count[proj.region] = region_count.get(proj.region, 0) + 1
                category_count[proj.category] = category_count.get(proj.category, 0) + 1
        if any(region_count.get(r, 0) > cap for r, cap in con.region_cap.items()):
            return False
        if any(category_count.get(c, 0) > cap for c, cap in con.category_cap.items()):
            return False
        # 累计普惠覆盖下限
        cumulative = 0.0
        for year in range(assumptions.horizon):
            cumulative += acc.annual_coverage.get(year, 0.0)
            if cumulative + _EPS < con.min_annual_coverage.get(year, 0.0):
                return False
        return True

    def dfs(i: int) -> None:
        nonlocal truncated
        if truncated:
            return
        if i == len(projects):
            if not feasible():
                return
            if len(plans) >= _MAX_PLANS:
                truncated = True
                return
            plans.append(_build_plan(assumptions, chosen, commits, acc,
                                     cost_overrun, demand_shock))
            return
        proj = projects[i]
        n = len(proj.phases)
        min_k = commits.get(proj.code, 0)
        # 确定性分支顺序：完整建成 -> 最短前缀 -> 不入选
        ks = list(range(n, min_k - 1, -1))
        if min_k == 0 and 0 not in ks:
            ks.append(0)
        for k in ks:
            frame = apply(proj, k) if k > 0 else ([], 0.0, 0.0)
            if frame is None:
                continue
            chosen[proj.code] = k
            dfs(i + 1)
            chosen.pop(proj.code, None)
            if frame[0] or frame[1] or frame[2]:
                undo(frame)

    dfs(0)

    if not plans:
        raise InfeasibleError(
            "在年度预算、覆盖下限、区域上限、依赖与承诺约束下不存在可行组合"
        )

    ordered = sorted(plans, key=_sort_key)[:top_k]
    return [
        _rerank(plan, rank, commits, cost_overrun, demand_shock, truncated)
        for rank, plan in enumerate(ordered, start=1)
    ]


def _sort_key(plan: Plan) -> tuple:
    # 降序指标取负；规范键兜底，保证并列解排序稳定且与输入顺序无关
    return (
        -round(plan.score, 9),
        -round(plan.total_nominal_coverage, 9),
        round(plan.total_nominal_cost, 9),
        plan.canonical_key(),
    )


def _rerank(plan: Plan, rank: int, commits: dict[str, int],
            cost_overrun: float, demand_shock: float,
            truncated: bool) -> Plan:
    return Plan(
        plan_digest=plan.plan_digest,
        assumption_digest=plan.assumption_digest,
        assumption_label=plan.assumption_label,
        selections=plan.selections,
        rank=rank,
        score=plan.score,
        total_nominal_cost=plan.total_nominal_cost,
        total_nominal_coverage=plan.total_nominal_coverage,
        total_nominal_industrial=plan.total_nominal_industrial,
        annual_cost=dict(sorted(plan.annual_cost.items())),
        annual_coverage=dict(sorted(plan.annual_coverage.items())),
        exit_loss=plan.exit_loss,
        reasons=plan.reasons,
        commitments=dict(sorted(commits.items())),
        cost_overrun=cost_overrun,
        demand_shock=demand_shock,
        truncated=plan.truncated,
    )


def _build_plan(assumptions: AssumptionSet, chosen: dict[str, int],
                commits: dict[str, int], acc: _Accum,
                cost_overrun: float, demand_shock: float) -> Plan:
    pmap = assumptions.project_map

    depended_on: dict[str, set[str]] = {}
    for proj in assumptions.projects:
        for dep in proj.depends_on:
            depended_on.setdefault(dep, set()).add(proj.code)

    selections = [Selection(code, chosen[code])
                  for code in sorted(chosen)]
    reasons: list[ItemReason] = []
    nominal_cost = nominal_cov = nominal_ind = 0.0

    for sel in selections:
        code, k = sel.code, sel.prefix
        if k == 0:
            continue
        proj = pmap[code]
        phases = proj.phases[:k]
        cost = sum(ph.cost for ph in phases)
        cov = sum(ph.coverage for ph in phases)
        ind = sum(ph.industrial for ph in phases)
        exit_loss = sum(ph.exit_loss for ph in phases) if k < len(proj.phases) else 0.0
        contribution = _prefix_score(proj, k, assumptions, cost_overrun, demand_shock)
        nominal_cost += cost
        nominal_cov += cov
        nominal_ind += ind

        roles: list[str] = []
        rationale: list[str] = []
        picked_followers = sorted(
            f for f in depended_on.get(code, ()) if chosen.get(f, 0)
        )
        if picked_followers:
            roles.append("被依赖前置")
            rationale.append(f"为 {', '.join(picked_followers)} 提供前置阶段")
        if proj.depends_on:
            roles.append("依赖方")
            rationale.append(
                f"前置 {', '.join(sorted(proj.depends_on))} 已先期投运"
            )
        blocked = sorted(x for x in proj.excludes if chosen.get(x, 0) == 0)
        if blocked:
            roles.append("互斥取舍")
            rationale.append(f"与 {', '.join(blocked)} 互斥，本方案取此路线")
        if k < len(proj.phases):
            roles.append("分期在建")
            rationale.append(
                f"仅承诺前 {k}/{len(proj.phases)} 期，敞口退出损失 {exit_loss:.6g}"
            )
        else:
            rationale.append(f"全部 {len(proj.phases)} 期建成，无退出损失")
        if commits.get(code, 0) > 0:
            roles.append("不可撤销承诺")
            rationale.append(f"委员会已锁定前 {commits[code]} 期")
        if proj.maturity < 1.0:
            rationale.append(f"技术成熟度 {proj.maturity:g}，收益已按此折扣")
        rationale.append(
            f"折现得分贡献 {contribution:.6g}；名义投入 {cost:.6g}、"
            f"覆盖 {cov:.6g}、产业带动 {ind:.6g}"
        )

        reasons.append(ItemReason(
            code=code,
            name=proj.name,
            category=proj.category,
            region=proj.region,
            prefix=k,
            total_phases=len(proj.phases),
            committed_prefix=commits.get(code, 0),
            maturity=proj.maturity,
            coverage=cov,
            industrial=ind,
            nominal_cost=cost,
            score_contribution=contribution,
            exit_loss=exit_loss,
            roles=tuple(roles),
            rationale=tuple(rationale),
        ))

    digest = stable_digest({
        "assumption": assumptions.digest(),
        "commitments": dict(sorted(commits.items())),
        "selections": [{"code": s.code, "prefix": s.prefix} for s in selections],
        "cost_overrun": cost_overrun,
        "demand_shock": demand_shock,
    })

    return Plan(
        plan_digest=digest,
        assumption_digest=assumptions.digest(),
        assumption_label=assumptions.label,
        selections=tuple(selections),
        rank=0,
        score=acc.score,
        total_nominal_cost=nominal_cost,
        total_nominal_coverage=nominal_cov,
        total_nominal_industrial=nominal_ind,
        annual_cost={y: v for y, v in sorted(acc.annual_cost.items()) if v},
        annual_coverage={y: v for y, v in sorted(acc.annual_coverage.items()) if v},
        exit_loss=acc.exit_loss,
        reasons=tuple(reasons),
        commitments=dict(sorted(commits.items())),
        cost_overrun=cost_overrun,
        demand_shock=demand_shock,
    )
