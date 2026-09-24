"""批处理接口：以 JSON 友好的字典驱动全部用例。

每个操作返回 {"status": "ok", "data": ...} 或 {"status": "error", "error": {...}}；
submit 按顺序执行一批操作，单个失败不影响其余操作。数字一律以字符串输出，
与内部 Decimal 口径一致，保证审计可逐位核对。
"""

from __future__ import annotations

from ..application.services import PortfolioService
from ..domain.errors import DomainError, ValidationError
from ..domain.models import (
    AssumptionSet,
    AuditResult,
    Commitment,
    Plan,
    PlanBatch,
    PlanMetrics,
    SensitivityReport,
    fmt,
    to_decimal,
)
from ..domain.scenarios import Scenario


class BatchAPI:
    """组合决策的批处理入口。"""

    def __init__(self, service: PortfolioService) -> None:
        self._service = service
        self._handlers = {
            "create_assumptions": self._create_assumptions,
            "derive_assumptions": self._derive_assumptions,
            "list_versions": self._list_versions,
            "commit_stages": self._commit_stages,
            "list_commitments": self._list_commitments,
            "generate_plans": self._generate_plans,
            "run_sensitivity": self._run_sensitivity,
            "audit": self._audit,
        }

    def handle(self, action: str, payload: dict | None = None) -> dict:
        """执行单个操作，业务错误以 error 状态返回而不抛出。"""
        payload = payload or {}
        try:
            handler = self._handlers.get(action)
            if handler is None:
                raise ValidationError(f"未知操作: {action}")
            return {"status": "ok", "action": action, "data": handler(payload)}
        except DomainError as exc:
            return {
                "status": "error",
                "action": action,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }

    def submit(self, jobs: list[dict]) -> list[dict]:
        """顺序执行一批操作；任一操作失败不中断后续操作。"""
        results = []
        for index, job in enumerate(jobs):
            action = job.get("action")
            try:
                result = self.handle(action, job.get("payload"))
            except Exception as exc:  # 兜底：批处理任务不应整体崩溃
                result = {
                    "status": "error",
                    "action": action,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            result["job_id"] = job.get("job_id", f"job-{index + 1}")
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # 各操作
    # ------------------------------------------------------------------
    def _create_assumptions(self, payload: dict) -> dict:
        return _assumption_view(self._service.create_assumptions(payload))

    def _derive_assumptions(self, payload: dict) -> dict:
        return _assumption_view(
            self._service.derive_assumptions(
                base_version=_as_int(_require(payload, "base_version"), "base_version"),
                changes=payload.get("changes", {}),
                label=payload.get("label"),
                change_note=payload.get("change_note", ""),
            )
        )

    def _list_versions(self, payload: dict) -> dict:
        return {"versions": list(self._service.list_versions())}

    def _commit_stages(self, payload: dict) -> dict:
        return _commitment_view(
            self._service.commit_stages(
                assumption_version=_as_int(_require(payload, "assumption_version"), "assumption_version"),
                project_id=str(_require(payload, "project_id")),
                stage_names=[str(name) for name in _require(payload, "stage_names")],
                decided_by=str(payload.get("decided_by", "")),
                note=str(payload.get("note", "")),
            )
        )

    def _list_commitments(self, payload: dict) -> dict:
        return {"commitments": [_commitment_view(c) for c in self._service.list_commitments()]}

    def _generate_plans(self, payload: dict) -> dict:
        return _batch_view(
            self._service.generate_plans(
                assumption_version=_as_int(_require(payload, "assumption_version"), "assumption_version"),
                max_plans=_as_int(payload.get("max_plans", 5), "max_plans"),
            )
        )

    def _run_sensitivity(self, payload: dict) -> dict:
        scenario_payload = _require(payload, "scenario")
        scenario = Scenario(
            name=str(_require(scenario_payload, "name")),
            description=str(scenario_payload.get("description", "")),
            cost_factor=to_decimal(scenario_payload.get("cost_factor", 1), "成本系数"),
            benefit_factor=to_decimal(scenario_payload.get("benefit_factor", 1), "收益系数"),
            project_cost_factors=_factor_map(scenario_payload.get("project_cost_factors", {})),
            project_benefit_factors=_factor_map(scenario_payload.get("project_benefit_factors", {})),
        )
        return _report_view(
            self._service.run_sensitivity(batch_id=str(_require(payload, "batch_id")), scenario=scenario)
        )

    def _audit(self, payload: dict) -> dict:
        if "report_id" in payload:
            result = self._service.audit_report_metric(
                report_id=str(payload["report_id"]),
                plan_id=str(_require(payload, "plan_id")),
                metric=str(_require(payload, "metric")),
            )
        else:
            result = self._service.audit_plan_metric(
                batch_id=str(_require(payload, "batch_id")),
                plan_id=str(_require(payload, "plan_id")),
                metric=str(_require(payload, "metric")),
                project_id=payload.get("project_id"),
            )
        return _audit_view(result)


# ----------------------------------------------------------------------
# 视图与工具
# ----------------------------------------------------------------------
def _require(payload: dict, key: str):
    if key not in payload:
        raise ValidationError(f"缺少必填字段: {key}")
    return payload[key]


def _as_int(value, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} 不是有效整数: {value!r}") from None


def _factor_map(raw: dict) -> tuple[tuple[str, object], ...]:
    return tuple(sorted((str(pid), to_decimal(value, "工程级系数")) for pid, value in raw.items()))


def _assumption_view(assumptions: AssumptionSet) -> dict:
    return {
        "version": assumptions.version,
        "parent_version": assumptions.parent_version,
        "label": assumptions.label,
        "change_note": assumptions.change_note,
        "created_at": assumptions.created_at,
        "project_count": len(assumptions.projects),
    }


def _commitment_view(commitment: Commitment) -> dict:
    return {
        "commitment_id": commitment.commitment_id,
        "assumption_version": commitment.assumption_version,
        "project_id": commitment.project_id,
        "stage_names": list(commitment.stage_names),
        "decided_by": commitment.decided_by,
        "decided_at": commitment.decided_at,
        "note": commitment.note,
    }


def _metrics_view(metrics: PlanMetrics) -> dict:
    return {
        "total_cost": fmt(metrics.total_cost),
        "coverage": fmt(metrics.coverage),
        "industry": fmt(metrics.industry),
        "exit_penalty": fmt(metrics.exit_penalty),
        "net_value": fmt(metrics.net_value),
        "cost_by_year": {str(year): fmt(cost) for year, cost in metrics.cost_by_year},
        "cost_by_region": {region: fmt(cost) for region, cost in metrics.cost_by_region},
    }


def _plan_view(plan: Plan) -> dict:
    return {
        "plan_id": plan.plan_id,
        "rank": plan.rank,
        "tied": plan.tied,
        "metrics": _metrics_view(plan.metrics),
        "items": [
            {
                "project_id": item.project_id,
                "stages": list(item.stage_names),
                "cost": fmt(item.cost),
                "coverage": fmt(item.coverage),
                "industry": fmt(item.industry),
                "contribution": fmt(item.contribution),
                "rationale": list(item.rationale),
            }
            for item in plan.items
        ],
        "notes": list(plan.notes),
        "excluded_notes": dict(plan.excluded_notes),
    }


def _batch_view(batch: PlanBatch) -> dict:
    return {
        "batch_id": batch.batch_id,
        "assumption_version": batch.assumption_version,
        "generated_at": batch.generated_at,
        "feasible": batch.feasible,
        "diagnostics": list(batch.diagnostics),
        "committed": {pid: list(names) for pid, names in batch.committed_snapshot},
        "plans": [_plan_view(plan) for plan in batch.plans],
    }


def _report_view(report: SensitivityReport) -> dict:
    scenario = report.scenario
    return {
        "report_id": report.report_id,
        "batch_id": report.batch_id,
        "assumption_version": report.assumption_version,
        "generated_at": report.generated_at,
        "scenario": {
            "name": scenario.name,
            "description": scenario.description,
            "cost_factor": fmt(scenario.cost_factor),
            "benefit_factor": fmt(scenario.benefit_factor),
            "project_cost_factors": {pid: fmt(v) for pid, v in scenario.project_cost_factors},
            "project_benefit_factors": {pid: fmt(v) for pid, v in scenario.project_benefit_factors},
        },
        "results": [
            {
                "plan_id": result.plan_id,
                "base_net_value": fmt(result.base_net_value),
                "scenario_net_value": fmt(result.scenario_net_value),
                "delta": fmt(result.delta),
                "feasible": result.feasible,
                "violations": list(result.violations),
            }
            for result in report.results
        ],
        "scenario_optimal": {
            "feasible": report.optimal_feasible,
            "net_value": fmt(report.optimal_net_value) if report.optimal_net_value is not None else None,
            "selection": dict(report.optimal_selection),
        },
        "diagnostics": list(report.diagnostics),
    }


def _audit_view(result: AuditResult) -> dict:
    return {
        "subject": result.subject,
        "source_id": result.source_id,
        "plan_id": result.plan_id,
        "project_id": result.project_id,
        "metric": result.metric,
        "value": fmt(result.value),
        "recomputed": fmt(result.recomputed),
        "verified": result.verified,
        "assumption_version": result.assumption_version,
        "scenario_name": result.scenario_name,
        "formula": result.formula,
        "inputs": [{"name": name, "value": value} for name, value in result.inputs],
    }
