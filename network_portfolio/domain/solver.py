"""候选组合的确定性枚举求解器。

枚举每个工程的建设期数（前缀选择），在年度预算、区域上限、互斥、前置依赖与
已承诺阶段的硬约束下做分支限界，最后按 (净值降序, 总成本升序, 选择字典序)
稳定排序。净值完全相同的方案构成并列解，名次相同、顺序确定。
"""

from __future__ import annotations

from decimal import Decimal

from .dependencies import build_prereq_map, topological_order
from .errors import SearchSpaceTooLargeError
from .models import ZERO, AssumptionSet, PlanMetrics
from .scoring import evaluate_selection, feasibility_violations, prefix_stats

DEFAULT_MAX_NODES = 2_000_000


class ScoredSelection:
    """一个可行选择及其指标。"""

    __slots__ = ("selection", "metrics")

    def __init__(self, selection: dict[str, int], metrics: PlanMetrics) -> None:
        self.selection = selection
        self.metrics = metrics


def _selection_key(selection: dict[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(selection.items()))


def _sort_key(scored: ScoredSelection) -> tuple:
    return (-scored.metrics.net_value, scored.metrics.total_cost, _selection_key(scored.selection))


def solve_portfolio(
    assumptions: AssumptionSet,
    committed_counts: dict[str, int],
    *,
    keep: int = 64,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> tuple[list[ScoredSelection], list[str]]:
    """求解并返回 (按名次排序的可行选择列表, 不可行时的诊断信息)。"""
    projects = assumptions.project_map()
    constraints = assumptions.constraints
    weights = assumptions.weights
    order = topological_order(sorted(projects), constraints.prerequisites)
    prereqs = build_prereq_map(constraints.prerequisites)
    budget = constraints.budget_map()
    caps = constraints.region_cap_map()

    # 每个工程的可选期数（已承诺工程从承诺期数起步）及单工程净价值
    options: dict[str, tuple[tuple[int, Decimal, Decimal], ...]] = {}
    best_value: dict[str, Decimal] = {}
    for pid in order:
        project = projects[pid]
        committed = committed_counts.get(pid, 0)
        start = committed if committed > 0 else 0
        project_options = []
        for count in range(start, len(project.stages) + 1):
            cost, cov, ind = prefix_stats(project, count)
            value = (weights.coverage * cov + weights.industry * ind) * project.maturity - weights.cost * cost
            project_options.append((count, cost, value))
        options[pid] = tuple(project_options)
        best_value[pid] = max(value for _, _, value in project_options)
    # 后缀最优上界：忽略共享扣减与退出损失，只会高估不会低估
    suffix_best = [ZERO] * (len(order) + 1)
    for i in range(len(order) - 1, -1, -1):
        suffix_best[i] = suffix_best[i + 1] + max(ZERO, best_value[order[i]])

    mutex_of: dict[str, frozenset[str]] = {pid: frozenset() for pid in order}
    for group in constraints.mutex_groups:
        for pid in group:
            mutex_of[pid] = mutex_of[pid] | frozenset(q for q in group if q != pid)

    selection: dict[str, int] = {}
    cost_by_year: dict[int, Decimal] = {}
    cost_by_region: dict[str, Decimal] = {}
    kept: list[ScoredSelection] = []
    worst_net: list[Decimal | None] = [None]  # 当前保留集合中最差净值，用于限界
    nodes = [0]

    def refresh_worst() -> None:
        if len(kept) < keep:
            worst_net[0] = None
        else:
            worst_net[0] = max(kept, key=_sort_key).metrics.net_value

    def record(metrics: PlanMetrics) -> None:
        candidate = ScoredSelection({k: v for k, v in selection.items() if v > 0}, metrics)
        if len(kept) < keep:
            kept.append(candidate)
        else:
            worst = max(kept, key=_sort_key)
            if _sort_key(candidate) < _sort_key(worst):
                kept.remove(worst)
                kept.append(candidate)
            else:
                return
        refresh_worst()

    def dfs(index: int, value_so_far: Decimal) -> None:
        nodes[0] += 1
        if nodes[0] > max_nodes:
            raise SearchSpaceTooLargeError(f"组合搜索空间超过上限 {max_nodes}，请收窄候选工程或加强约束")
        bound = worst_net[0]
        if bound is not None and value_so_far + suffix_best[index] < bound:
            return
        if index == len(order):
            metrics = evaluate_selection(assumptions, selection, committed_counts)
            if metrics.coverage >= constraints.min_coverage:
                record(metrics)
            return
        pid = order[index]
        project = projects[pid]
        # 前置工程必须先完整建成，否则本工程只能不选
        blocked = any(selection.get(pre, 0) < len(projects[pre].stages) for pre in prereqs.get(pid, ()))
        for count, cost, value in options[pid]:
            if count > 0:
                if blocked:
                    continue
                if any(selection.get(other, 0) > 0 for other in mutex_of[pid]):
                    continue
                stage_costs: dict[int, Decimal] = {}
                affordable = True
                for stage in project.stages[:count]:
                    year = stage.year
                    stage_costs[year] = stage_costs.get(year, ZERO) + stage.cost
                    if year in budget and cost_by_year.get(year, ZERO) + stage_costs[year] > budget[year]:
                        affordable = False
                        break
                if not affordable:
                    continue
                if project.region in caps and cost_by_region.get(project.region, ZERO) + cost > caps[project.region]:
                    continue
            else:
                stage_costs = {}
            selection[pid] = count
            for year, amount in stage_costs.items():
                cost_by_year[year] = cost_by_year.get(year, ZERO) + amount
            if count > 0:
                cost_by_region[project.region] = cost_by_region.get(project.region, ZERO) + cost
            dfs(index + 1, value_so_far + value)
            del selection[pid]
            for year, amount in stage_costs.items():
                cost_by_year[year] -= amount
            if count > 0:
                cost_by_region[project.region] -= cost

    dfs(0, ZERO)
    kept.sort(key=_sort_key)
    if kept:
        return kept, []
    return [], _diagnostics(assumptions, committed_counts)


def _diagnostics(assumptions: AssumptionSet, committed_counts: dict[str, int]) -> list[str]:
    """无可行方案时给出面向委员会的排查线索。"""
    messages: list[str] = []
    committed_only = {pid: count for pid, count in committed_counts.items() if count > 0}
    if committed_only:
        violations = feasibility_violations(assumptions, committed_only, committed_counts)
        if violations:
            messages.append("仅已承诺阶段即不可行: " + "；".join(violations))
    everything = {p.project_id: len(p.stages) for p in assumptions.projects}
    max_coverage = evaluate_selection(assumptions, everything, committed_counts).coverage
    if max_coverage < assumptions.constraints.min_coverage:
        messages.append(
            f"全部候选工程建成后的普惠覆盖约为 {max_coverage}，"
            f"仍低于最低要求 {assumptions.constraints.min_coverage}"
        )
    if not messages:
        messages.append("在年度预算与区域上限约束下，不存在满足最低普惠覆盖要求的组合")
    return messages
