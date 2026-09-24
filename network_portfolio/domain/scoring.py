"""方案指标计算与可行性检查。

口径约定：
- 覆盖/产业指标为名义值，共享收益的重复部分只计一次；
- 目标净值中收益按技术成熟度折算，最低普惠覆盖约束使用名义覆盖；
- 退出损失针对"已承诺但未全部建成"的工程，按工程计一次。
"""

from __future__ import annotations

from decimal import Decimal

from .dependencies import build_prereq_map
from .models import ZERO, AssumptionSet, PlanMetrics, Project, fmt


def prefix_stats(project: Project, count: int) -> tuple[Decimal, Decimal, Decimal]:
    """前 count 个阶段的 (成本, 覆盖收益, 产业带动) 合计。"""
    cost = ZERO
    coverage = ZERO
    industry = ZERO
    for stage in project.stages[:count]:
        cost += stage.cost
        coverage += stage.coverage
        industry += stage.industry
    return cost, coverage, industry


def evaluate_selection(
    assumptions: AssumptionSet,
    selection: dict[str, int],
    committed_counts: dict[str, int],
) -> PlanMetrics:
    """计算一个选择的全部指标。

    selection: project_id -> 已选阶段数（前缀）；committed_counts 用于退出损失判定。
    """
    projects = assumptions.project_map()
    weights = assumptions.weights
    total_cost = ZERO
    coverage = ZERO
    industry = ZERO
    adjusted = ZERO  # 成熟度折算后的加权收益
    by_year: dict[int, Decimal] = {}
    by_region: dict[str, Decimal] = {}
    for pid in sorted(selection):
        count = selection[pid]
        if count <= 0:
            continue
        project = projects[pid]
        cost, cov, ind = prefix_stats(project, count)
        total_cost += cost
        coverage += cov
        industry += ind
        adjusted += (weights.coverage * cov + weights.industry * ind) * project.maturity
        for stage in project.stages[:count]:
            by_year[stage.year] = by_year.get(stage.year, ZERO) + stage.cost
        by_region[project.region] = by_region.get(project.region, ZERO) + cost
    # 共享收益：组内每多一个入选工程，重复部分多扣一次，最终只计一次
    for group in assumptions.shared_benefits:
        chosen = sum(1 for pid in group.project_ids if selection.get(pid, 0) > 0)
        if chosen >= 2:
            extra = Decimal(chosen - 1)
            coverage -= group.coverage_overlap * extra
            industry -= group.industry_overlap * extra
            group_maturity = min(projects[pid].maturity for pid in group.project_ids)
            adjusted -= (
                (weights.coverage * group.coverage_overlap + weights.industry * group.industry_overlap)
                * group_maturity
                * extra
            )
    # 退出损失：已承诺但未全部建成的工程
    exit_penalty = ZERO
    for pid, committed in committed_counts.items():
        if committed > 0:
            project = projects[pid]
            if selection.get(pid, 0) < len(project.stages):
                exit_penalty += project.exit_loss
    net_value = adjusted - weights.cost * total_cost - exit_penalty
    return PlanMetrics(
        total_cost=total_cost,
        coverage=coverage,
        industry=industry,
        exit_penalty=exit_penalty,
        net_value=net_value,
        cost_by_year=tuple(sorted(by_year.items())),
        cost_by_region=tuple(sorted(by_region.items())),
    )


def feasibility_violations(
    assumptions: AssumptionSet,
    selection: dict[str, int],
    committed_counts: dict[str, int],
    metrics: PlanMetrics | None = None,
) -> tuple[str, ...]:
    """返回选择违反的约束描述；空元组表示可行。"""
    if metrics is None:
        metrics = evaluate_selection(assumptions, selection, committed_counts)
    projects = assumptions.project_map()
    constraints = assumptions.constraints
    violations: list[str] = []
    for pid, committed in sorted(committed_counts.items()):
        if committed > 0 and selection.get(pid, 0) < committed:
            violations.append(f"工程 {pid} 的前 {committed} 个阶段已承诺为不可撤销，必须纳入")
    prereqs = build_prereq_map(constraints.prerequisites)
    for dependent, pre_list in sorted(prereqs.items()):
        if selection.get(dependent, 0) > 0:
            for pre in pre_list:
                if selection.get(pre, 0) < len(projects[pre].stages):
                    violations.append(f"工程 {dependent} 的前置工程 {pre} 必须完整建成")
    for group in constraints.mutex_groups:
        chosen = [pid for pid in group if selection.get(pid, 0) > 0]
        if len(chosen) > 1:
            violations.append(f"互斥组内只能入选一个工程: {'、'.join(sorted(chosen))}")
    budget = constraints.budget_map()
    for year, cost in metrics.cost_by_year:
        limit = budget.get(year)
        if limit is not None and cost > limit:
            violations.append(f"{year} 年成本 {fmt(cost)} 超出年度预算 {fmt(limit)}")
    caps = constraints.region_cap_map()
    for region, cost in metrics.cost_by_region:
        limit = caps.get(region)
        if limit is not None and cost > limit:
            violations.append(f"区域 {region} 成本 {fmt(cost)} 超出区域上限 {fmt(limit)}")
    if metrics.coverage < constraints.min_coverage:
        violations.append(f"普惠覆盖 {fmt(metrics.coverage)} 低于最低要求 {fmt(constraints.min_coverage)}")
    return tuple(violations)
