"""测试共用夹具。"""

from network_portfolio.domain import (
    AssumptionSet,
    Constraints,
    Phase,
    Project,
    Scenario,
)


def mobile_project() -> Project:
    return Project(
        code="MOBILE",
        name="移动增强工程",
        category="mobile",
        region="east",
        phases=(
            Phase(year=0, cost=10.0, coverage=5.0, industrial=8.0, exit_loss=3.0),
            Phase(year=1, cost=8.0, coverage=4.0, industrial=6.0, exit_loss=3.0),
        ),
        maturity=0.9,
    )


def satellite_project() -> Project:
    return Project(
        code="SATELLITE",
        name="卫星补盲工程",
        category="satellite",
        region="west",
        # 依赖移动增强先期投运
        phases=(Phase(year=0, cost=6.0, coverage=3.0, industrial=2.0),),
        depends_on=frozenset({"MOBILE"}),
    )


def fiber_project() -> Project:
    return Project(
        code="FIBER",
        name="万兆光网工程",
        category="fiber",
        region="east",
        phases=(Phase(year=0, cost=9.0, coverage=6.0, industrial=4.0),),
        # 与移动增强在城东重复覆盖，互斥
        excludes=frozenset({"MOBILE"}),
    )


def compute_project() -> Project:
    return Project(
        code="COMPUTE",
        name="算力节点工程",
        category="compute",
        region="north",
        phases=(
            Phase(year=0, cost=7.0, coverage=1.0, industrial=7.0),
            Phase(year=1, cost=7.0, coverage=1.0, industrial=7.0),
        ),
        maturity=0.8,
    )


def standard_constraints() -> Constraints:
    return Constraints(
        annual_budget={0: 25.0, 1: 20.0},
        min_annual_coverage={0: 4.0},
        region_cap={"east": 2, "west": 1, "north": 1},
    )


def standard_scenarios() -> tuple[Scenario, ...]:
    return (
        Scenario(code="DEMAND_DOWN", name="需求下修20%", demand_shock=-0.2),
        Scenario(code="COST_OVERRUN", name="成本超支30%", cost_overrun=0.3),
        Scenario(code="STRESS", name="需求下修且成本超支",
                 demand_shock=-0.3, cost_overrun=0.25),
    )


def standard_assumptions(*, label: str = "v1",
                         scenarios: tuple[Scenario, ...] | None = None) -> AssumptionSet:
    return AssumptionSet(
        label=label,
        projects=(mobile_project(), satellite_project(),
                  fiber_project(), compute_project()),
        constraints=standard_constraints(),
        horizon=2,
        scenarios=standard_scenarios() if scenarios is None else scenarios,
    )
