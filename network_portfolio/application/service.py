"""组合决策应用服务：版本登记、承诺锁定、批处理求解与审计还原。

所有写入均为追加式不可变记录：假设版本一经引用即永不改写，
新版本只能通过 ``derive_assumptions`` 派生。
"""

from __future__ import annotations

from typing import Any, Mapping

from ..domain import AssumptionSet, Constraints
from ..engine import sensitivity, solve
from .codec import decode_assumptions
from .ports import Clock, IdGenerator
from .records import AssumptionRecord, AuditTrace, BatchRecord, CommitmentRecord
from .repository import PortfolioRepository
from .serializers import plan_to_dict, scenario_to_dict


class PortfolioService:
    def __init__(self, repository: PortfolioRepository,
                 clock: Clock, id_generator: IdGenerator) -> None:
        self._repo = repository
        self._clock = clock
        self._ids = id_generator

    # ------------------------------------------------------------------ 版本

    def register_assumptions(self, assumptions: AssumptionSet, *,
                             note: str = "") -> AssumptionRecord:
        """登记一版假设；相同摘要幂等返回既有记录，绝不覆盖。"""

        digest = assumptions.digest()
        existing = self._repo.find_assumption_by_digest(digest)
        if existing is not None:
            return existing
        record = AssumptionRecord(
            version_id=self._ids.next_id("version"),
            digest=digest,
            label=assumptions.label,
            created_at=self._clock.now(),
            snapshot=assumptions.digest_payload(),
            note=note,
        )
        self._repo.save_assumption(record)
        return record

    def derive_assumptions(self, parent_version_id: str, *,
                           label: str | None = None,
                           projects=None,
                           constraints: Constraints | None = None,
                           horizon: int | None = None,
                           weight_industrial: float | None = None,
                           weight_coverage: float | None = None,
                           discount_rate: float | None = None,
                           scenarios=None,
                           note: str = "") -> AssumptionRecord:
        """基于历史版本派生新版假设（父版本保持不变并被引用留痕）。"""

        parent = self._require_version(parent_version_id)
        parent_set = decode_assumptions(parent.snapshot)
        child = parent_set.derive(
            label=label, projects=projects, constraints=constraints,
            horizon=horizon, weight_industrial=weight_industrial,
            weight_coverage=weight_coverage, discount_rate=discount_rate,
            scenarios=scenarios,
        )
        digest = child.digest()
        if digest == parent.digest:
            # 内容未变：复用父版本，杜绝"换壳新版本"
            return parent
        existing = self._repo.find_assumption_by_digest(digest)
        if existing is not None:
            return existing
        record = AssumptionRecord(
            version_id=self._ids.next_id("version"),
            digest=digest,
            label=child.label,
            created_at=self._clock.now(),
            snapshot=child.digest_payload(),
            parent_version_id=parent.version_id,
            parent_digest=parent.digest,
            note=note,
        )
        self._repo.save_assumption(record)
        return record

    def load_assumptions(self, version_id: str) -> AssumptionSet:
        return decode_assumptions(self._require_version(version_id).snapshot)

    # ------------------------------------------------------------------ 承诺

    def commit(self, version_id: str, commitments: Mapping[str, int], *,
               note: str = "") -> CommitmentRecord:
        """对某假设版本登记不可撤销阶段承诺（追加式，不修改既有承诺）。"""

        self._require_version(version_id)
        assumptions = self.load_assumptions(version_id)
        # 借求解器的入参校验提前拒绝非法承诺
        solve(assumptions, dict(commitments), top_k=1)
        record = CommitmentRecord(
            decision_id=self._ids.next_id("decision"),
            version_id=version_id,
            created_at=self._clock.now(),
            commitments=dict(commitments),
            note=note,
        )
        self._repo.save_commitment(record)
        return record

    def merged_commitments(self, version_id: str) -> dict[str, int]:
        """合并某版本的全部历史承诺：各工程取最大锁定前缀（只增不减）。"""

        merged: dict[str, int] = {}
        for record in self._repo.list_commitments(version_id):
            for code, prefix in record.commitments.items():
                merged[code] = max(merged.get(code, 0), prefix)
        return merged

    # ---------------------------------------------------------------- 批处理

    def run_batch(self, version_id: str, *, top_k: int = 5,
                  commitments: Mapping[str, int] | None = None,
                  include_sensitivity: bool = True,
                  note: str = "") -> BatchRecord:
        """对指定历史版本运行批处理：稳定排序方案 + 逐项理由 + 情景敏感性。"""

        version = self._require_version(version_id)
        assumptions = decode_assumptions(version.snapshot)
        effective = self.merged_commitments(version_id)
        if commitments:
            for code, prefix in commitments.items():
                effective[code] = max(effective.get(code, 0), prefix)

        plans = solve(assumptions, effective, top_k=top_k)
        scenario_results = (
            sensitivity(assumptions, effective, top_k=1)
            if include_sensitivity else []
        )
        record = BatchRecord(
            batch_id=self._ids.next_id("batch"),
            created_at=self._clock.now(),
            assumption_version_id=version.version_id,
            assumption_digest=version.digest,
            assumption_label=version.label,
            commitments=dict(sorted(effective.items())),
            top_k=top_k,
            plans=tuple(plan_to_dict(p) for p in plans),
            sensitivity=tuple(scenario_to_dict(s) for s in scenario_results),
            note=note,
        )
        self._repo.save_batch(record)
        return record

    # ------------------------------------------------------------------ 审计

    def audit(self, batch_id: str, metric: str, *,
              plan_rank: int = 1) -> AuditTrace:
        """还原批处理方案中任一数字所依据的数据版本与计算公式。

        ``metric`` 取值：
        - ``score``：方案总折现得分；
        - ``totals.<nominal_cost|nominal_coverage|nominal_industrial|exit_loss>``；
        - ``annual_cost.<年>`` / ``annual_coverage.<年>``（情景调整后口径）；
        - ``item.<工程编码>.<nominal_cost|coverage|industrial|exit_loss|score_contribution>``。
        """

        batch = self._repo.get_batch(batch_id)
        if batch is None:
            raise KeyError(f"批处理 {batch_id} 不存在")
        if not 1 <= plan_rank <= len(batch.plans):
            raise KeyError(f"方案名次 {plan_rank} 超出范围")
        plan_dict = batch.plans[plan_rank - 1]
        version = self._require_version(batch.assumption_version_id)
        assumptions = decode_assumptions(version.snapshot)
        value, evidence, formula = _explain_metric(
            metric, plan_dict, assumptions
        )
        return AuditTrace(
            batch_id=batch_id,
            plan_digest=plan_dict["plan_digest"],
            assumption_version_id=version.version_id,
            assumption_digest=version.digest,
            metric=metric,
            value=value,
            evidence=evidence,
            formula=formula,
        )

    # ----------------------------------------------------------------- 内部

    def _require_version(self, version_id: str) -> AssumptionRecord:
        record = self._repo.get_assumption(version_id)
        if record is None:
            raise KeyError(f"假设版本 {version_id} 不存在")
        return record


# --------------------------------------------------------------------- 审计计算

_PHASE_FIELDS = {
    "nominal_cost": ("cost", False),
    "coverage": ("coverage", False),
    "nominal_coverage": ("coverage", False),
    "industrial": ("industrial", False),
    "nominal_industrial": ("industrial", False),
    "exit_loss": ("exit_loss", False),
}


def _snapshot_index(assumptions: AssumptionSet) -> dict[str, dict[str, Any]]:
    return {p["code"]: p for p in assumptions.digest_payload()["projects"]}


def _explain_metric(metric: str, plan: dict[str, Any],
                    assumptions: AssumptionSet) -> tuple[float, list[dict[str, Any]], str]:
    snap = _snapshot_index(assumptions)
    overrun = float(plan["scenario"]["cost_overrun"])
    shock = float(plan["scenario"]["demand_shock"])
    selected = {s["code"]: s["prefix"] for s in plan["selections"] if s["prefix"]}

    if metric == "score":
        return _explain_score(plan, assumptions, snap, selected, overrun, shock)

    if metric.startswith("totals."):
        field = metric.split(".", 1)[1]
        return _explain_total(field, plan, snap, selected, overrun, shock,
                              assumptions)

    if metric.startswith("annual_cost.") or metric.startswith("annual_coverage."):
        kind, year_raw = metric.split(".", 1)
        year = int(year_raw)
        return _explain_annual(kind, year, snap, selected, overrun, shock)

    if metric.startswith("item."):
        _, code, field = metric.split(".", 2)
        if code not in selected:
            raise KeyError(f"工程 {code} 未入选该方案")
        return _explain_item(field, code, snap[code], selected[code],
                             assumptions, overrun, shock)

    raise KeyError(f"无法识别的审计指标 {metric!r}")


def _evidence_rows(code: str, snap_project: dict[str, Any], prefix: int,
                   fields: tuple[str, ...], overrun: float,
                   shock: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ph in snap_project["phases"][:prefix]:
        row: dict[str, Any] = {"project": code, "year": ph["year"]}
        for f in fields:
            raw = float(ph.get(f, 0.0))
            row[f"raw_{f}"] = raw
            if f == "cost":
                row[f"effective_{f}"] = raw * (1.0 + overrun)
            elif f in ("coverage", "industrial"):
                row[f"effective_{f}"] = raw * (1.0 + shock)
            else:
                row[f"effective_{f}"] = raw
        rows.append(row)
    return rows


def _explain_total(field, plan, snap, selected, overrun, shock, assumptions):
    mapping = {
        "nominal_cost": ("cost", "名义成本 = Σ入选阶段原始成本（不含情景乘数）"),
        "nominal_coverage": ("coverage", "名义覆盖 = Σ入选阶段原始覆盖（不含情景乘数）"),
        "nominal_industrial": ("industrial", "名义产业带动 = Σ入选阶段原始值（不含情景乘数）"),
        "exit_loss": ("exit_loss", "退出损失 = 仅对入选而未全部建成的工程，Σ其已选阶段退出损失"),
    }
    if field not in mapping:
        raise KeyError(f"未知总指标 totals.{field}")
    raw_field, formula = mapping[field]
    evidence: list[dict[str, Any]] = []
    total = 0.0
    for code, prefix in selected.items():
        if field == "exit_loss":
            proj = assumptions.project_map[code]
            if prefix >= len(proj.phases):
                continue
        rows = _evidence_rows(code, snap[code], prefix, (raw_field,), overrun, shock)
        evidence.extend(rows)
        total += sum(r[f"raw_{raw_field}"] for r in rows)
    return total, evidence, formula


def _explain_annual(kind, year, snap, selected, overrun, shock):
    raw_field = "cost" if kind == "annual_cost" else "coverage"
    evidence: list[dict[str, Any]] = []
    total = 0.0
    for code, prefix in selected.items():
        for row in _evidence_rows(code, snap[code], prefix, (raw_field,),
                                  overrun, shock):
            if row["year"] == year:
                evidence.append(row)
                total += row[f"effective_{raw_field}"]
    multiplier = ("成本乘数 (1+cost_overrun)" if kind == "annual_cost"
                  else "需求乘数 (1+demand_shock)")
    formula = f"{year} 年金额 = Σ该年入选阶段原始{ '成本' if kind=='annual_cost' else '覆盖'} × {multiplier}"
    return total, evidence, formula


def _explain_item(field, code, snap_project, prefix, assumptions, overrun, shock):
    if field in ("nominal_cost", "coverage", "industrial", "exit_loss"):
        raw_field = {"nominal_cost": "cost"}.get(field, field)
        if field == "exit_loss" and prefix >= len(assumptions.project_map[code].phases):
            return 0.0, [], "工程已全部建成，退出损失为 0"
        rows = _evidence_rows(code, snap_project, prefix, (raw_field,),
                              overrun, shock)
        total = sum(r[f"raw_{raw_field}"] for r in rows)
        formula = f"工程 {code} 前 {prefix} 期 {field} = Σ各阶段原始值"
        return total, rows, formula
    if field == "score_contribution":
        single = _single_project_plan(code, prefix, overrun, shock)
        return _explain_score(
            single, assumptions,
            {code: snap_project}, {code: prefix}, overrun, shock,
            only=(code, prefix),
        )
    raise KeyError(f"未知工程指标 item.{code}.{field}")


def _single_project_plan(code: str, prefix: int,
                         overrun: float, shock: float) -> dict[str, Any]:
    return {
        "plan_digest": "",
        "scenario": {"cost_overrun": overrun, "demand_shock": shock},
        "selections": [{"code": code, "prefix": prefix}],
    }


def _explain_score(plan, assumptions, snap, selected, overrun, shock,
                   only=None) -> tuple[float, list[dict[str, Any]], str]:
    rate = assumptions.discount_rate
    w_cov = assumptions.weight_coverage
    w_ind = assumptions.weight_industrial
    evidence: list[dict[str, Any]] = []
    total = 0.0
    for code, prefix in selected.items():
        if only and code != only[0]:
            continue
        proj = assumptions.project_map[code]
        for idx, ph in enumerate(proj.phases[:prefix], start=1):
            df = 1.0 / ((1.0 + rate) ** ph.year)
            eff_cov = ph.coverage * (1.0 + shock)
            eff_ind = ph.industrial * (1.0 + shock)
            contribution = df * proj.maturity * (w_cov * eff_cov + w_ind * eff_ind)
            evidence.append({
                "project": code, "year": ph.year, "phase_index": idx,
                "raw_coverage": ph.coverage, "raw_industrial": ph.industrial,
                "maturity": proj.maturity, "discount_factor": df,
                "effective_coverage": eff_cov, "effective_industrial": eff_ind,
                "score_contribution": contribution,
            })
            total += contribution
        if 0 < prefix < len(proj.phases):
            penalty_raw = sum(p.exit_loss for p in proj.phases[:prefix])
            df = 1.0 / ((1.0 + rate) ** proj.phases[prefix - 1].year)
            penalty = df * penalty_raw
            evidence.append({
                "project": code, "year": proj.phases[prefix - 1].year,
                "raw_exit_loss": penalty_raw, "discount_factor": df,
                "score_contribution": -penalty,
            })
            total -= penalty
    formula = (
        "得分 = Σ 折现因子 1/(1+r)^年 × 成熟度 × "
        "(覆盖权重×有效覆盖 + 产业权重×有效产业带动) "
        "− Σ 未建成工程的折现退出损失"
    )
    return total, evidence, formula
