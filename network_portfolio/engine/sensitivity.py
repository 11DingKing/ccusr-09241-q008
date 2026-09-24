"""情景敏感性：对需求下修、成本超支等情景重优化并对照基准。"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import AssumptionSet, Scenario
from . import solver
from .solver import Plan


@dataclass(frozen=True)
class ScenarioResult:
    scenario_code: str
    scenario_name: str
    cost_overrun: float
    demand_shock: float
    best: Plan
    # 与基准最优方案的差异（同口径重算基准入选集合，不重新排序）
    baseline_selection: tuple[tuple[str, int], ...]
    flipped: bool


def sensitivity(
    assumptions: AssumptionSet,
    commitments: dict[str, int] | None = None,
    *,
    top_k: int = 3,
) -> list[ScenarioResult]:
    """对假设集中登记的全部情景逐一重优化。

    基准（无冲击）总是作为第一个结果返回，便于审计对照。
    """

    results: list[ScenarioResult] = []
    baseline = solver.solve(assumptions, commitments, top_k=1)[0]
    baseline_key = tuple((s.code, s.prefix) for s in baseline.selections if s.prefix)
    results.append(ScenarioResult(
        scenario_code="BASELINE",
        scenario_name="基准假设",
        cost_overrun=0.0,
        demand_shock=0.0,
        best=baseline,
        baseline_selection=baseline_key,
        flipped=False,
    ))
    for scenario in sorted(assumptions.scenarios, key=lambda s: s.code):
        best = solver.solve(
            assumptions, commitments, top_k=1,
            cost_overrun=scenario.cost_overrun,
            demand_shock=scenario.demand_shock,
        )[0]
        key = tuple((s.code, s.prefix) for s in best.selections if s.prefix)
        results.append(ScenarioResult(
            scenario_code=scenario.code,
            scenario_name=scenario.name,
            cost_overrun=scenario.cost_overrun,
            demand_shock=scenario.demand_shock,
            best=best,
            baseline_selection=baseline_key,
            flipped=key != baseline_key,
        ))
    return results
