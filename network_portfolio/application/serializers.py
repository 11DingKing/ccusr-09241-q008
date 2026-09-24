"""引擎结果到留档/接口 JSON 结构的确定性序列化。"""

from __future__ import annotations

from typing import Any

from ..engine import Plan, ScenarioResult


def plan_to_dict(plan: Plan) -> dict[str, Any]:
    return {
        "plan_digest": plan.plan_digest,
        "rank": plan.rank,
        "assumption_digest": plan.assumption_digest,
        "assumption_label": plan.assumption_label,
        "score": plan.score,
        "totals": {
            "nominal_cost": plan.total_nominal_cost,
            "nominal_coverage": plan.total_nominal_coverage,
            "nominal_industrial": plan.total_nominal_industrial,
            "exit_loss": plan.exit_loss,
        },
        "scenario": {
            "cost_overrun": plan.cost_overrun,
            "demand_shock": plan.demand_shock,
        },
        "annual_cost": {str(y): v for y, v in plan.annual_cost.items()},
        "annual_coverage": {str(y): v for y, v in plan.annual_coverage.items()},
        "commitments": dict(plan.commitments),
        "selections": [
            {"code": s.code, "prefix": s.prefix, "selected": s.selected}
            for s in plan.selections
        ],
        "reasons": [
            {
                "code": r.code,
                "name": r.name,
                "category": r.category,
                "region": r.region,
                "prefix": r.prefix,
                "total_phases": r.total_phases,
                "committed_prefix": r.committed_prefix,
                "maturity": r.maturity,
                "coverage": r.coverage,
                "industrial": r.industrial,
                "nominal_cost": r.nominal_cost,
                "score_contribution": r.score_contribution,
                "exit_loss": r.exit_loss,
                "roles": list(r.roles),
                "rationale": list(r.rationale),
            }
            for r in plan.reasons
        ],
        "truncated": plan.truncated,
    }


def scenario_to_dict(result: ScenarioResult) -> dict[str, Any]:
    return {
        "scenario_code": result.scenario_code,
        "scenario_name": result.scenario_name,
        "cost_overrun": result.cost_overrun,
        "demand_shock": result.demand_shock,
        "flipped": result.flipped,
        "best": plan_to_dict(result.best),
    }
