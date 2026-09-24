"""敏感性情景：对基准假设的乘性扰动及其应用。

典型用法：需求下修（benefit_factor < 1）、成本超支（cost_factor > 1），
也可按工程单独设置系数。情景只作用于阶段的成本与收益数字；
技术成熟度、退出损失与约束条件不随情景变化。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from .models import ONE, AssumptionSet, Stage


@dataclass(frozen=True)
class Scenario:
    """一组乘性系数：全局系数 × 工程级系数。"""

    name: str
    description: str = ""
    cost_factor: Decimal = ONE
    benefit_factor: Decimal = ONE
    project_cost_factors: tuple[tuple[str, Decimal], ...] = ()
    project_benefit_factors: tuple[tuple[str, Decimal], ...] = ()


def apply_scenario(assumptions: AssumptionSet, scenario: Scenario) -> AssumptionSet:
    """返回应用情景后的假设副本；不修改原版本，也不产生新的持久化版本。"""
    cost_overrides = dict(scenario.project_cost_factors)
    benefit_overrides = dict(scenario.project_benefit_factors)
    projects = []
    for project in assumptions.projects:
        cost_factor = scenario.cost_factor * cost_overrides.get(project.project_id, ONE)
        benefit_factor = scenario.benefit_factor * benefit_overrides.get(project.project_id, ONE)
        stages = tuple(
            Stage(
                name=stage.name,
                year=stage.year,
                cost=stage.cost * cost_factor,
                coverage=stage.coverage * benefit_factor,
                industry=stage.industry * benefit_factor,
            )
            for stage in project.stages
        )
        projects.append(replace(project, stages=stages))
    return replace(
        assumptions,
        projects=tuple(projects),
        label=f"{assumptions.label}（情景：{scenario.name}）",
    )
