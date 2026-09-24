"""组合决策的应用服务：假设版本、分期承诺、方案生成、敏感性分析与审计。"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Optional

from ..domain.dependencies import find_cycle
from ..domain.errors import (
    CommitmentConflictError,
    CyclicDependencyError,
    NotFoundError,
    ValidationError,
)
from ..domain.models import (
    ONE,
    ZERO,
    AssumptionSet,
    AuditResult,
    Commitment,
    Constraints,
    Plan,
    PlanBatch,
    PlanItem,
    PlanScenarioResult,
    Project,
    SensitivityReport,
    SharedBenefit,
    Stage,
    Weights,
    fmt,
    to_decimal,
)
from ..domain.rationale import build_excluded_notes, build_item_rationale, plan_notes
from ..domain.scenarios import Scenario, apply_scenario
from ..domain.scoring import evaluate_selection, feasibility_violations, prefix_stats
from ..domain.solver import DEFAULT_MAX_NODES, solve_portfolio
from .ports import (
    AssumptionRepository,
    Clock,
    CommitmentRepository,
    IdGenerator,
    PlanBatchRepository,
    SensitivityReportRepository,
)

PLAN_METRICS = ("total_cost", "coverage", "industry", "exit_penalty", "net_value")
PROJECT_METRICS = ("cost", "coverage", "industry", "contribution")
REPORT_METRICS = ("base_net_value", "scenario_net_value", "delta")


class PortfolioService:
    """组合决策用例入口。所有写操作只新增、不改写历史。"""

    def __init__(
        self,
        assumptions: AssumptionRepository,
        batches: PlanBatchRepository,
        commitments: CommitmentRepository,
        reports: SensitivityReportRepository,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._assumptions = assumptions
        self._batches = batches
        self._commitments = commitments
        self._reports = reports
        self._clock = clock
        self._ids = ids

    # ------------------------------------------------------------------
    # 假设版本
    # ------------------------------------------------------------------
    def create_assumptions(self, payload: dict) -> AssumptionSet:
        """以完整数据创建一版新假设（谱系根）。"""
        projects, shared, constraints, weights = self._parse_payload(payload)
        assumptions = self._assemble(
            parent_version=None,
            label=str(payload.get("label") or "未命名假设"),
            change_note=str(payload.get("change_note") or "初始版本"),
            projects=projects,
            shared=shared,
            constraints=constraints,
            weights=weights,
        )
        self._assumptions.add(assumptions)
        return assumptions

    def derive_assumptions(
        self,
        *,
        base_version: int,
        changes: dict,
        label: Optional[str] = None,
        change_note: str = "",
    ) -> AssumptionSet:
        """基于既有版本派生新版本（假设更正）。原版本保持不变。

        changes 支持的键：
        - projects: {"add": [...], "update": [...], "remove": [工程id, ...]}，更新为整体替换
        - shared_benefits / constraints / weights: 整体替换
        """
        base = self._assumptions.get(base_version)
        unknown = set(changes) - {"projects", "shared_benefits", "constraints", "weights"}
        if unknown:
            raise ValidationError(f"不支持的变更项: {sorted(unknown)}")
        projects = list(base.projects)
        project_changes = changes.get("projects", {})
        unknown_ops = set(project_changes) - {"add", "update", "remove"}
        if unknown_ops:
            raise ValidationError(f"不支持的工程变更操作: {sorted(unknown_ops)}")
        by_id = {p.project_id: p for p in projects}
        for pid in project_changes.get("remove", []):
            if pid not in by_id:
                raise NotFoundError(f"待移除的工程不存在: {pid}")
            projects = [p for p in projects if p.project_id != pid]
            del by_id[pid]
        for data in project_changes.get("update", []):
            project = _parse_project(data)
            if project.project_id not in by_id:
                raise NotFoundError(f"待更新的工程不存在: {project.project_id}")
            projects = [project if p.project_id == project.project_id else p for p in projects]
            by_id[project.project_id] = project
        for data in project_changes.get("add", []):
            project = _parse_project(data)
            if project.project_id in by_id:
                raise ValidationError(f"工程已存在: {project.project_id}")
            projects.append(project)
            by_id[project.project_id] = project
        shared = (
            tuple(_parse_shared_benefit(item) for item in changes["shared_benefits"])
            if "shared_benefits" in changes
            else base.shared_benefits
        )
        constraints = _parse_constraints(changes["constraints"]) if "constraints" in changes else base.constraints
        weights = _parse_weights(changes["weights"]) if "weights" in changes else base.weights
        assumptions = self._assemble(
            parent_version=base_version,
            label=label or f"{base.label}（修订）",
            change_note=change_note or f"基于版本 {base_version} 派生",
            projects=tuple(projects),
            shared=shared,
            constraints=constraints,
            weights=weights,
        )
        self._assumptions.add(assumptions)
        return assumptions

    def list_versions(self) -> tuple[int, ...]:
        return self._assumptions.versions()

    def get_assumptions(self, version: int) -> AssumptionSet:
        return self._assumptions.get(version)

    def _assemble(
        self,
        *,
        parent_version: Optional[int],
        label: str,
        change_note: str,
        projects: tuple[Project, ...],
        shared: tuple[SharedBenefit, ...],
        constraints: Constraints,
        weights: Weights,
    ) -> AssumptionSet:
        _validate_assumptions(projects, shared, constraints)
        return AssumptionSet(
            version=self._assumptions.next_version(),
            parent_version=parent_version,
            label=label,
            change_note=change_note,
            projects=projects,
            shared_benefits=shared,
            constraints=constraints,
            weights=weights,
            created_at=self._clock.now(),
        )

    def _parse_payload(
        self, payload: dict
    ) -> tuple[tuple[Project, ...], tuple[SharedBenefit, ...], Constraints, Weights]:
        projects = tuple(_parse_project(item) for item in payload.get("projects", []))
        if not projects:
            raise ValidationError("候选工程不能为空")
        shared = tuple(_parse_shared_benefit(item) for item in payload.get("shared_benefits", []))
        constraints = _parse_constraints(payload.get("constraints", {}))
        weights = _parse_weights(payload.get("weights", {}))
        return projects, shared, constraints, weights

    # ------------------------------------------------------------------
    # 分期承诺
    # ------------------------------------------------------------------
    def commit_stages(
        self,
        *,
        assumption_version: int,
        project_id: str,
        stage_names: list[str],
        decided_by: str = "",
        note: str = "",
    ) -> Commitment:
        """把某工程的若干阶段承诺为不可撤销。承诺跨假设版本持续有效。"""
        assumptions = self._assumptions.get(assumption_version)
        project = assumptions.project_map().get(project_id)
        if project is None:
            raise NotFoundError(f"工程不存在: {project_id}")
        if not stage_names:
            raise ValidationError("承诺阶段不能为空")
        known = [stage.name for stage in project.stages]
        missing = [name for name in stage_names if name not in known]
        if missing:
            raise ValidationError(f"工程 {project_id} 不存在阶段: {missing}")
        commitment = Commitment(
            commitment_id=self._ids.next("cmt"),
            assumption_version=assumptions.version,
            project_id=project_id,
            stage_names=tuple(dict.fromkeys(stage_names)),
            decided_by=decided_by,
            decided_at=self._clock.now(),
            note=note,
        )
        self._commitments.add(commitment)
        return commitment

    def list_commitments(self) -> tuple[Commitment, ...]:
        return self._commitments.all()

    def _committed_maps(
        self, assumptions: AssumptionSet
    ) -> tuple[dict[str, tuple[str, ...]], dict[str, int]]:
        """汇总全部承诺，并按前缀归一：承诺第 k 期即承诺前 k 期。"""
        projects = assumptions.project_map()
        names: dict[str, tuple[str, ...]] = {}
        for commitment in self._commitments.all():
            project = projects.get(commitment.project_id)
            if project is None:
                raise CommitmentConflictError(
                    f"已承诺工程 {commitment.project_id} 在假设版本 {assumptions.version} 中不存在"
                )
            known = [stage.name for stage in project.stages]
            missing = [name for name in commitment.stage_names if name not in known]
            if missing:
                raise CommitmentConflictError(
                    f"已承诺阶段 {missing} 在假设版本 {assumptions.version} 的"
                    f"工程 {commitment.project_id} 中不存在"
                )
            merged = set(names.get(commitment.project_id, ())) | set(commitment.stage_names)
            prefix = max(known.index(name) for name in merged) + 1
            names[commitment.project_id] = tuple(known[:prefix])
        counts = {pid: len(stage_names) for pid, stage_names in names.items()}
        return names, counts

    # ------------------------------------------------------------------
    # 方案生成
    # ------------------------------------------------------------------
    def generate_plans(
        self,
        *,
        assumption_version: int,
        max_plans: int = 5,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> PlanBatch:
        """在全部硬约束下生成稳定排序的可比较方案；边界并列一并保留。"""
        if max_plans < 1:
            raise ValidationError("max_plans 至少为 1")
        assumptions = self._assumptions.get(assumption_version)
        committed_names, committed_counts = self._committed_maps(assumptions)
        scored, diagnostics = solve_portfolio(
            assumptions,
            committed_counts,
            keep=max(64, max_plans * 8),
            max_nodes=max_nodes,
        )
        batch_id = self._ids.next("batch")
        snapshot = tuple(sorted(committed_names.items()))
        if not scored:
            batch = PlanBatch(
                batch_id=batch_id,
                assumption_version=assumptions.version,
                generated_at=self._clock.now(),
                feasible=False,
                plans=(),
                diagnostics=tuple(diagnostics),
                committed_snapshot=snapshot,
            )
            self._batches.add(batch)
            return batch
        chosen = []
        for index, candidate in enumerate(scored):
            if index >= max_plans and candidate.metrics.net_value != scored[index - 1].metrics.net_value:
                break
            chosen.append(candidate)
        ranks: list[int] = []
        for index, candidate in enumerate(chosen):
            if index > 0 and candidate.metrics.net_value == chosen[index - 1].metrics.net_value:
                ranks.append(ranks[-1])
            else:
                ranks.append(index + 1)
        tally = Counter(candidate.metrics.net_value for candidate in chosen)
        project_map = assumptions.project_map()
        plans = []
        for candidate, rank in zip(chosen, ranks):
            selection = candidate.selection
            rationale = build_item_rationale(assumptions, selection, committed_names, candidate.metrics)
            excluded = build_excluded_notes(assumptions, selection, committed_counts)
            items = tuple(
                PlanItem(
                    project_id=pid,
                    stage_names=tuple(stage.name for stage in project_map[pid].stages[:count]),
                    cost=prefix_stats(project_map[pid], count)[0],
                    coverage=prefix_stats(project_map[pid], count)[1],
                    industry=prefix_stats(project_map[pid], count)[2],
                    contribution=rationale[pid][0],
                    rationale=rationale[pid][1],
                )
                for pid, count in sorted(selection.items())
                if count > 0
            )
            plans.append(
                Plan(
                    plan_id=self._ids.next("plan"),
                    rank=rank,
                    tied=tally[candidate.metrics.net_value] > 1,
                    items=items,
                    metrics=candidate.metrics,
                    notes=plan_notes(assumptions, candidate.metrics),
                    excluded_notes=tuple(sorted(excluded.items())),
                )
            )
        batch = PlanBatch(
            batch_id=batch_id,
            assumption_version=assumptions.version,
            generated_at=self._clock.now(),
            feasible=True,
            plans=tuple(plans),
            diagnostics=(),
            committed_snapshot=snapshot,
        )
        self._batches.add(batch)
        return batch

    def get_batch(self, batch_id: str) -> PlanBatch:
        return self._batches.get(batch_id)

    # ------------------------------------------------------------------
    # 敏感性分析
    # ------------------------------------------------------------------
    def run_sensitivity(self, *, batch_id: str, scenario: Scenario) -> SensitivityReport:
        """对批次内每个方案按情景重估，并在情景下重新寻优。"""
        batch = self._batches.get(batch_id)
        if not batch.feasible:
            raise ValidationError(f"批次 {batch_id} 无可行方案，无法开展敏感性分析")
        base = self._assumptions.get(batch.assumption_version)
        scaled = apply_scenario(base, scenario)
        committed = batch.committed_counts()
        results = []
        for plan in batch.plans:
            selection = plan.selection
            metrics = evaluate_selection(scaled, selection, committed)
            violations = feasibility_violations(scaled, selection, committed, metrics)
            results.append(
                PlanScenarioResult(
                    plan_id=plan.plan_id,
                    base_net_value=plan.metrics.net_value,
                    scenario_net_value=metrics.net_value,
                    delta=metrics.net_value - plan.metrics.net_value,
                    feasible=not violations,
                    violations=violations,
                )
            )
        scored, diagnostics = solve_portfolio(scaled, committed, keep=1)
        if scored:
            optimal_selection = tuple(sorted(scored[0].selection.items()))
            optimal_net: Optional[Decimal] = scored[0].metrics.net_value
            optimal_feasible = True
        else:
            optimal_selection = ()
            optimal_net = None
            optimal_feasible = False
        report = SensitivityReport(
            report_id=self._ids.next("report"),
            batch_id=batch.batch_id,
            assumption_version=base.version,
            scenario_name=scenario.name,
            scenario=scenario,
            generated_at=self._clock.now(),
            results=tuple(results),
            optimal_selection=optimal_selection,
            optimal_net_value=optimal_net,
            optimal_feasible=optimal_feasible,
            diagnostics=tuple(diagnostics),
        )
        self._reports.add(report)
        return report

    # ------------------------------------------------------------------
    # 审计
    # ------------------------------------------------------------------
    def audit_plan_metric(
        self,
        *,
        batch_id: str,
        plan_id: str,
        metric: str,
        project_id: Optional[str] = None,
    ) -> AuditResult:
        """还原方案中任一数字所依据的数据版本，并重算校验。"""
        batch = self._batches.get(batch_id)
        plan = _find_plan(batch, plan_id)
        assumptions = self._assumptions.get(batch.assumption_version)
        committed = batch.committed_counts()
        selection = plan.selection
        metrics = evaluate_selection(assumptions, selection, committed)
        if project_id is None and metric in PLAN_METRICS:
            stored = getattr(plan.metrics, metric)
            recomputed = getattr(metrics, metric)
            inputs, formula = _plan_metric_inputs(assumptions, selection, committed, metric)
        elif project_id is not None and metric in PROJECT_METRICS:
            item = next((i for i in plan.items if i.project_id == project_id), None)
            if item is None:
                raise NotFoundError(f"方案 {plan_id} 中不包含工程 {project_id}")
            stored, recomputed, inputs, formula = _project_metric_inputs(
                assumptions, selection, committed, item, metric, metrics
            )
        else:
            raise ValidationError(f"不支持的审计指标: {metric}（project_id={'有' if project_id else '无'}）")
        return AuditResult(
            subject="plan",
            source_id=batch.batch_id,
            plan_id=plan_id,
            project_id=project_id,
            metric=metric,
            value=stored,
            recomputed=recomputed,
            verified=stored == recomputed,
            assumption_version=assumptions.version,
            scenario_name=None,
            formula=formula,
            inputs=inputs,
        )

    def audit_report_metric(self, *, report_id: str, plan_id: str, metric: str) -> AuditResult:
        """还原敏感性报告中任一数字所依据的数据版本与情景。"""
        report = self._reports.get(report_id)
        result = next((r for r in report.results if r.plan_id == plan_id), None)
        if result is None:
            raise NotFoundError(f"报告 {report_id} 中不包含方案 {plan_id}")
        if metric not in REPORT_METRICS:
            raise ValidationError(f"不支持的审计指标: {metric}")
        batch = self._batches.get(report.batch_id)
        plan = _find_plan(batch, plan_id)
        base = self._assumptions.get(report.assumption_version)
        scenario = report.scenario
        scaled = apply_scenario(base, scenario)
        scenario_metrics = evaluate_selection(scaled, plan.selection, batch.committed_counts())
        if metric == "scenario_net_value":
            stored, recomputed = result.scenario_net_value, scenario_metrics.net_value
        elif metric == "base_net_value":
            stored, recomputed = result.base_net_value, plan.metrics.net_value
        else:
            stored = result.delta
            recomputed = scenario_metrics.net_value - plan.metrics.net_value
        inputs = (
            ("情景", scenario.name),
            ("成本系数", fmt(scenario.cost_factor)),
            ("收益系数", fmt(scenario.benefit_factor)),
            ("基准净值", fmt(plan.metrics.net_value)),
            ("情景净值", fmt(scenario_metrics.net_value)),
        )
        return AuditResult(
            subject="sensitivity",
            source_id=report.report_id,
            plan_id=plan_id,
            project_id=None,
            metric=metric,
            value=stored,
            recomputed=recomputed,
            verified=stored == recomputed,
            assumption_version=base.version,
            scenario_name=scenario.name,
            formula="在版本化假设上应用情景系数后重算净值；delta = 情景净值 − 基准净值",
            inputs=inputs,
        )


# ----------------------------------------------------------------------
# 解析与校验
# ----------------------------------------------------------------------
def _parse_stage(data: dict, project_id: str) -> Stage:
    try:
        name = str(data["name"]).strip()
        year = int(data["year"])
        cost = to_decimal(data["cost"], "阶段成本")
        coverage = to_decimal(data["coverage"], "覆盖收益")
        industry = to_decimal(data["industry"], "产业带动")
    except KeyError as exc:
        raise ValidationError(f"工程 {project_id} 的阶段缺少字段: {exc}") from None
    except (TypeError, ValueError):
        raise ValidationError(f"工程 {project_id} 的阶段年份不是有效整数") from None
    if not name:
        raise ValidationError(f"工程 {project_id} 存在无名阶段")
    if not 1900 <= year <= 2200:
        raise ValidationError(f"工程 {project_id} 阶段 {name} 的年份超出合理范围: {year}")
    if cost < ZERO or coverage < ZERO or industry < ZERO:
        raise ValidationError(f"工程 {project_id} 阶段 {name} 的成本与收益不得为负")
    return Stage(name=name, year=year, cost=cost, coverage=coverage, industry=industry)


def _parse_project(data: dict) -> Project:
    try:
        project_id = str(data["project_id"]).strip()
        stages = tuple(_parse_stage(item, project_id or "?") for item in data["stages"])
    except KeyError as exc:
        raise ValidationError(f"工程缺少字段: {exc}") from None
    if not project_id:
        raise ValidationError("工程标识不能为空")
    if not stages:
        raise ValidationError(f"工程 {project_id} 至少需要一个阶段")
    stage_names = [stage.name for stage in stages]
    if len(set(stage_names)) != len(stage_names):
        raise ValidationError(f"工程 {project_id} 的阶段名称重复")
    maturity = to_decimal(data.get("maturity", ONE), "技术成熟度")
    if not ZERO <= maturity <= ONE:
        raise ValidationError(f"工程 {project_id} 的技术成熟度须在 0..1 之间")
    exit_loss = to_decimal(data.get("exit_loss", ZERO), "退出损失")
    if exit_loss < ZERO:
        raise ValidationError(f"工程 {project_id} 的退出损失不得为负")
    return Project(
        project_id=project_id,
        name=str(data.get("name") or project_id),
        category=str(data.get("category") or "未分类"),
        region=str(data.get("region") or "未分区"),
        maturity=maturity,
        exit_loss=exit_loss,
        stages=stages,
    )


def _parse_shared_benefit(data: dict) -> SharedBenefit:
    try:
        group_id = str(data["group_id"]).strip()
        project_ids = tuple(str(pid) for pid in data["project_ids"])
    except KeyError as exc:
        raise ValidationError(f"共享收益缺少字段: {exc}") from None
    coverage_overlap = to_decimal(data.get("coverage_overlap", ZERO), "共享覆盖重复部分")
    industry_overlap = to_decimal(data.get("industry_overlap", ZERO), "共享产业重复部分")
    return SharedBenefit(
        group_id=group_id,
        project_ids=project_ids,
        coverage_overlap=coverage_overlap,
        industry_overlap=industry_overlap,
    )


def _parse_constraints(data: dict) -> Constraints:
    data = data or {}
    budget: dict[int, Decimal] = {}
    for key, value in data.get("annual_budget", {}).items():
        try:
            year = int(key)
        except (TypeError, ValueError):
            raise ValidationError(f"年度预算的年份不是有效整数: {key!r}") from None
        amount = to_decimal(value, "年度预算")
        if amount < ZERO:
            raise ValidationError("年度预算不得为负")
        budget[year] = amount
    min_coverage = to_decimal(data.get("min_coverage", ZERO), "最低普惠覆盖")
    if min_coverage < ZERO:
        raise ValidationError("最低普惠覆盖不得为负")
    caps: dict[str, Decimal] = {}
    for region, value in data.get("regional_caps", {}).items():
        cap = to_decimal(value, "区域上限")
        if cap < ZERO:
            raise ValidationError(f"区域 {region} 上限不得为负")
        caps[str(region)] = cap
    prerequisites = tuple((str(dep), str(pre)) for dep, pre in data.get("prerequisites", []))
    mutex_groups = tuple(tuple(str(pid) for pid in group) for group in data.get("mutex_groups", []))
    return Constraints(
        annual_budget=tuple(sorted(budget.items())),
        min_coverage=min_coverage,
        regional_caps=tuple(sorted(caps.items())),
        prerequisites=prerequisites,
        mutex_groups=mutex_groups,
    )


def _parse_weights(data: dict) -> Weights:
    data = data or {}
    return Weights(
        coverage=to_decimal(data.get("coverage", ONE), "覆盖权重"),
        industry=to_decimal(data.get("industry", ONE), "产业权重"),
        cost=to_decimal(data.get("cost", ONE), "成本权重"),
    )


def _validate_assumptions(
    projects: tuple[Project, ...],
    shared: tuple[SharedBenefit, ...],
    constraints: Constraints,
) -> None:
    ids = [project.project_id for project in projects]
    if len(set(ids)) != len(ids):
        raise ValidationError("工程标识重复")
    known = set(ids)
    for dependent, prereq in constraints.prerequisites:
        if dependent not in known:
            raise ValidationError(f"前置依赖引用了不存在的工程: {dependent}")
        if prereq not in known:
            raise ValidationError(f"前置依赖引用了不存在的工程: {prereq}")
    cycle = find_cycle(sorted(known), constraints.prerequisites)
    if cycle:
        raise CyclicDependencyError(cycle)
    for group in constraints.mutex_groups:
        if len(group) < 2:
            raise ValidationError("互斥组至少包含两个工程")
        if len(set(group)) != len(group):
            raise ValidationError("互斥组内工程重复")
        for pid in group:
            if pid not in known:
                raise ValidationError(f"互斥组引用了不存在的工程: {pid}")
    for group in shared:
        if len(set(group.project_ids)) < 2:
            raise ValidationError(f"共享收益组 {group.group_id} 至少包含两个不同工程")
        for pid in group.project_ids:
            if pid not in known:
                raise ValidationError(f"共享收益组 {group.group_id} 引用了不存在的工程: {pid}")
        if group.coverage_overlap < ZERO or group.industry_overlap < ZERO:
            raise ValidationError(f"共享收益组 {group.group_id} 的重复部分不得为负")
    budget_years = {year for year, _ in constraints.annual_budget}
    missing = sorted({stage.year for project in projects for stage in project.stages} - budget_years)
    if missing:
        raise ValidationError(f"以下年份缺少年度预算: {missing}")


# ----------------------------------------------------------------------
# 审计辅助
# ----------------------------------------------------------------------
def _find_plan(batch: PlanBatch, plan_id: str) -> Plan:
    plan = next((p for p in batch.plans if p.plan_id == plan_id), None)
    if plan is None:
        raise NotFoundError(f"方案不存在: {plan_id}")
    return plan


def _plan_metric_inputs(
    assumptions: AssumptionSet,
    selection: dict[str, int],
    committed: dict[str, int],
    metric: str,
) -> tuple[tuple[tuple[str, str], ...], str]:
    weights = assumptions.weights
    projects = assumptions.project_map()
    inputs: list[tuple[str, str]] = []
    if metric == "net_value":
        inputs.extend(
            [
                ("权重·覆盖", fmt(weights.coverage)),
                ("权重·产业", fmt(weights.industry)),
                ("权重·成本", fmt(weights.cost)),
            ]
        )
        for pid in sorted(selection):
            if selection[pid] <= 0:
                continue
            project = projects[pid]
            cost, cov, ind = prefix_stats(project, selection[pid])
            inputs.append((f"{pid}·覆盖/产业/成熟度/成本", f"{fmt(cov)}/{fmt(ind)}/{fmt(project.maturity)}/{fmt(cost)}"))
        for group in assumptions.shared_benefits:
            chosen = sum(1 for pid in group.project_ids if selection.get(pid, 0) > 0)
            if chosen >= 2:
                inputs.append(
                    (f"共享组 {group.group_id} 重复扣减", f"{chosen - 1} 次（覆盖 {fmt(group.coverage_overlap)}、产业 {fmt(group.industry_overlap)}）")
                )
        formula = "net_value = Σ(覆盖×覆盖权重 + 产业×产业权重)×成熟度 − 共享重复×组内最低成熟度 − 成本权重×总成本 − 退出损失"
    elif metric == "total_cost":
        for pid in sorted(selection):
            if selection[pid] > 0:
                inputs.append((pid, fmt(prefix_stats(projects[pid], selection[pid])[0])))
        formula = "total_cost = Σ 各工程已选阶段成本"
    elif metric == "coverage":
        for pid in sorted(selection):
            if selection[pid] > 0:
                inputs.append((pid, fmt(prefix_stats(projects[pid], selection[pid])[1])))
        formula = "coverage = Σ 各工程已选阶段覆盖 − 共享重复（每多一个入选工程扣一次）"
    elif metric == "industry":
        for pid in sorted(selection):
            if selection[pid] > 0:
                inputs.append((pid, fmt(prefix_stats(projects[pid], selection[pid])[2])))
        formula = "industry = Σ 各工程已选阶段产业带动 − 共享重复（每多一个入选工程扣一次）"
    else:  # exit_penalty
        for pid, count in sorted(committed.items()):
            if count > 0 and selection.get(pid, 0) < len(projects[pid].stages):
                inputs.append((pid, fmt(projects[pid].exit_loss)))
        formula = "exit_penalty = Σ 已承诺但未全部建成工程的退出损失"
    return tuple(inputs), formula


def _project_metric_inputs(
    assumptions: AssumptionSet,
    selection: dict[str, int],
    committed: dict[str, int],
    item: PlanItem,
    metric: str,
    metrics,
) -> tuple[Decimal, Decimal, tuple[tuple[str, str], ...], str]:
    projects = assumptions.project_map()
    project = projects[item.project_id]
    count = selection[item.project_id]
    if metric in ("cost", "coverage", "industry"):
        index = {"cost": 0, "coverage": 1, "industry": 2}[metric]
        stage_values = [
            (stage.cost, stage.coverage, stage.industry)[index] for stage in project.stages[:count]
        ]
        stored = getattr(item, metric)
        recomputed = sum(stage_values, ZERO)
        inputs = tuple(
            (f"阶段 {stage.name}", fmt(value)) for stage, value in zip(project.stages[:count], stage_values)
        )
        formula = f"{metric} = Σ 已选阶段的{metric}（未含共享收益扣减，扣减在方案层面计算）"
        return stored, recomputed, inputs, formula
    # contribution：方案净值 − 移除该工程后的净值
    without = dict(selection)
    without[item.project_id] = 0
    metrics_without = evaluate_selection(assumptions, without, committed)
    stored = item.contribution
    recomputed = metrics.net_value - metrics_without.net_value
    inputs = (
        ("方案净值", fmt(metrics.net_value)),
        (f"移除 {item.project_id} 后净值", fmt(metrics_without.net_value)),
    )
    formula = "contribution = net_value(方案) − net_value(移除该工程后)"
    return stored, recomputed, inputs, formula
