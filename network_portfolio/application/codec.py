"""领域对象与 JSON 友好结构之间的确定性编解码。

编码结果同时作为假设版本的留档快照：审计时仅凭快照即可重建领域对象，
因此任何历史版本引用的数字都能还原到其原始数据。
"""

from __future__ import annotations

from typing import Any

from ..domain import (
    AssumptionSet,
    Constraints,
    Phase,
    Project,
    Scenario,
)


def decode_assumptions(payload: dict[str, Any]) -> AssumptionSet:
    """从留档快照重建不可变假设集。"""

    projects: list[Project] = []
    for raw in payload["projects"]:
        phases = tuple(
            Phase(
                year=int(ph["year"]),
                cost=float(ph["cost"]),
                coverage=float(ph.get("coverage", 0.0)),
                industrial=float(ph.get("industrial", 0.0)),
                exit_loss=float(ph.get("exit_loss", 0.0)),
            )
            for ph in raw["phases"]
        )
        projects.append(Project(
            code=raw["code"],
            name=raw["name"],
            category=raw["category"],
            region=raw["region"],
            phases=phases,
            maturity=float(raw.get("maturity", 1.0)),
            depends_on=frozenset(raw.get("depends_on", ())),
            excludes=frozenset(raw.get("excludes", ())),
        ))
    c_raw = payload["constraints"]
    constraints = Constraints(
        annual_budget={int(k): float(v) for k, v in c_raw["annual_budget"].items()},
        min_annual_coverage={
            int(k): float(v) for k, v in c_raw.get("min_annual_coverage", {}).items()
        },
        region_cap={k: int(v) for k, v in c_raw.get("region_cap", {}).items()},
        category_cap={k: int(v) for k, v in c_raw.get("category_cap", {}).items()},
    )
    scenarios = tuple(
        Scenario(
            code=s["code"],
            name=s["name"],
            cost_overrun=float(s.get("cost_overrun", 0.0)),
            demand_shock=float(s.get("demand_shock", 0.0)),
        )
        for s in payload.get("scenarios", ())
    )
    return AssumptionSet(
        label=payload["label"],
        projects=tuple(projects),
        constraints=constraints,
        horizon=int(payload["horizon"]),
        weight_industrial=float(payload.get("weight_industrial", 1.0)),
        weight_coverage=float(payload.get("weight_coverage", 1.0)),
        discount_rate=float(payload.get("discount_rate", 0.0)),
        scenarios=scenarios,
    )
