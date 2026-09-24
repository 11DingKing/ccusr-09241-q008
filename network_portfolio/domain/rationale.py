"""逐项入选理由与未入选说明的生成。"""

from __future__ import annotations

from decimal import Decimal

from .dependencies import prerequisite_closure
from .models import AssumptionSet, PlanMetrics, fmt
from .scoring import evaluate_selection, feasibility_violations, prefix_stats


def build_item_rationale(
    assumptions: AssumptionSet,
    selection: dict[str, int],
    committed_names: dict[str, tuple[str, ...]],
    metrics: PlanMetrics,
) -> dict[str, tuple[Decimal, tuple[str, ...]]]:
    """为每个入选工程生成 (边际净贡献, 理由列表)。"""
    projects = assumptions.project_map()
    constraints = assumptions.constraints
    committed_counts = {pid: len(names) for pid, names in committed_names.items()}
    dependents_of: dict[str, list[str]] = {}
    for dependent, prereq in constraints.prerequisites:
        dependents_of.setdefault(prereq, []).append(dependent)
    result: dict[str, tuple[Decimal, tuple[str, ...]]] = {}
    for pid, count in sorted(selection.items()):
        if count <= 0:
            continue
        project = projects[pid]
        lines: list[str] = []
        names = committed_names.get(pid, ())
        if names:
            lines.append(f"阶段{'、'.join(names)}已承诺为不可撤销")
        dependents = sorted(d for d in dependents_of.get(pid, []) if selection.get(d, 0) > 0)
        if dependents:
            lines.append(f"是 {'、'.join(dependents)} 的前置工程，必须同步建设")
        without = dict(selection)
        without[pid] = 0
        metrics_without = evaluate_selection(assumptions, without, committed_counts)
        if metrics.coverage >= constraints.min_coverage > metrics_without.coverage:
            lines.append(
                f"拆除后普惠覆盖 {fmt(metrics_without.coverage)} 将低于最低要求 {fmt(constraints.min_coverage)}"
            )
        for group in assumptions.shared_benefits:
            if pid in group.project_ids:
                others = sorted(x for x in group.project_ids if x != pid and selection.get(x, 0) > 0)
                if others:
                    lines.append(f"与 {'、'.join(others)} 存在共享收益，重复部分已扣除")
        if count < len(project.stages):
            if names:
                lines.append(f"工程未全部建成，已计退出损失 {fmt(project.exit_loss)}")
            else:
                lines.append(f"仅建设前 {count} 期，后续阶段未纳入本方案")
        contribution = metrics.net_value - metrics_without.net_value
        cost, cov, ind = prefix_stats(project, count)
        lines.append(
            f"边际净贡献 {fmt(contribution)}（覆盖 {fmt(cov)}、产业 {fmt(ind)}、"
            f"成熟度 {fmt(project.maturity)}、成本 {fmt(cost)}）"
        )
        result[pid] = (contribution, tuple(lines))
    return result


def build_excluded_notes(
    assumptions: AssumptionSet,
    selection: dict[str, int],
    committed_counts: dict[str, int],
) -> dict[str, str]:
    """为未入选工程生成一句话说明：被哪条约束挡住，或边际净贡献为何不值。"""
    projects = assumptions.project_map()
    weights = assumptions.weights
    base = evaluate_selection(assumptions, selection, committed_counts)
    notes: dict[str, str] = {}
    for pid in sorted(projects):
        if selection.get(pid, 0) > 0:
            continue
        project = projects[pid]
        mutex_hit = None
        for group in assumptions.constraints.mutex_groups:
            if pid in group:
                for other in group:
                    if other != pid and selection.get(other, 0) > 0:
                        mutex_hit = other
        if mutex_hit is not None:
            notes[pid] = f"与已入选工程 {mutex_hit} 互斥"
            continue
        # 该工程单看最有利的建设期数
        best_count = 1
        best_value = None
        for count in range(1, len(project.stages) + 1):
            cost, cov, ind = prefix_stats(project, count)
            value = (weights.coverage * cov + weights.industry * ind) * project.maturity - weights.cost * cost
            if best_value is None or value > best_value:
                best_value, best_count = value, count
        trial = dict(selection)
        trial[pid] = best_count
        for pre in sorted(prerequisite_closure(assumptions.constraints.prerequisites, pid)):
            if trial.get(pre, 0) < len(projects[pre].stages):
                trial[pre] = len(projects[pre].stages)
        trial_metrics = evaluate_selection(assumptions, trial, committed_counts)
        violations = feasibility_violations(assumptions, trial, committed_counts, trial_metrics)
        blocking = [v for v in violations if "普惠覆盖" not in v]  # 纳入只会提升覆盖
        if blocking:
            notes[pid] = f"受约束限制无法纳入: {blocking[0]}"
        else:
            marginal = trial_metrics.net_value - base.net_value
            notes[pid] = f"单独纳入的边际净贡献为 {fmt(marginal)}"
    return notes


def plan_notes(assumptions: AssumptionSet, metrics: PlanMetrics) -> tuple[str, ...]:
    """方案层面的约束达成摘要。"""
    constraints = assumptions.constraints
    notes = [f"普惠覆盖 {fmt(metrics.coverage)}（最低要求 {fmt(constraints.min_coverage)}）"]
    budget = constraints.budget_map()
    for year, cost in metrics.cost_by_year:
        if year in budget:
            notes.append(f"{year} 年成本 {fmt(cost)} / 预算 {fmt(budget[year])}")
    caps = constraints.region_cap_map()
    for region, cost in metrics.cost_by_region:
        if region in caps:
            notes.append(f"区域 {region} 成本 {fmt(cost)} / 上限 {fmt(caps[region])}")
    if metrics.exit_penalty > 0:
        notes.append(f"含退出损失 {fmt(metrics.exit_penalty)}")
    return tuple(notes)
