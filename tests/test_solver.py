"""求解引擎：并列解、稳定排序、约束与互斥测试。"""

import unittest

from network_portfolio.domain import (
    AssumptionSet,
    Constraints,
    Phase,
    Project,
)
from network_portfolio.engine import InfeasibleError, solve
from tests.fixtures import (
    fiber_project,
    mobile_project,
    satellite_project,
    standard_assumptions,
    standard_constraints,
)


class ConstraintTests(unittest.TestCase):
    def test_dependency_requires_upstream_selected(self) -> None:
        ass = standard_assumptions()
        for plan in solve(ass, top_k=10):
            selected = plan.selected_codes
            if "SATELLITE" in selected:
                self.assertIn("MOBILE", selected)

    def test_exclusion_never_co_selects(self) -> None:
        ass = standard_assumptions()
        for plan in solve(ass, top_k=20):
            self.assertFalse(
                {"MOBILE", "FIBER"} <= plan.selected_codes,
                "互斥工程不得同时入选",
            )

    def test_coverage_floor_filters_plans(self) -> None:
        ass = standard_assumptions()
        for plan in solve(ass, top_k=20):
            cum = 0.0
            for year in range(ass.horizon):
                cum += plan.annual_coverage.get(year, 0.0)
                self.assertGreaterEqual(
                    cum, ass.constraints.min_annual_coverage.get(year, 0.0) - 1e-9
                )

    def test_annual_budget_respected(self) -> None:
        ass = standard_assumptions()
        for plan in solve(ass, top_k=20):
            for year, cap in ass.constraints.annual_budget.items():
                self.assertLessEqual(plan.annual_cost.get(year, 0.0), cap + 1e-9)

    def test_region_cap_respected(self) -> None:
        ass = standard_assumptions()
        for plan in solve(ass, top_k=20):
            regions = {}
            for code in plan.selected_codes:
                region = ass.project_map[code].region
                regions[region] = regions.get(region, 0) + 1
            for region, cap in ass.constraints.region_cap.items():
                self.assertLessEqual(regions.get(region, 0), cap)

    def test_infeasible_when_coverage_floor_too_high(self) -> None:
        projects = (mobile_project(),)
        con = Constraints(annual_budget={0: 100.0, 1: 100.0},
                          min_annual_coverage={0: 999.0})
        ass = AssumptionSet("impossible", projects, con, 2)
        with self.assertRaises(InfeasibleError):
            solve(ass)

    def test_infeasible_when_commitment_exceeds_budget(self) -> None:
        ass = standard_assumptions()
        # 两期全锁，第 1 年 8 > 预算（无其他腾挪空间时卫星也挤预算）
        con = Constraints(annual_budget={0: 9.0, 1: 7.0},
                          min_annual_coverage={0: 4.0})
        tight = AssumptionSet("tight", ass.projects, con, 2)
        with self.assertRaises(InfeasibleError):
            solve(tight, {"MOBILE": 2})


class StableOrderingTests(unittest.TestCase):
    def test_ranks_are_sorted_by_score_desc(self) -> None:
        plans = solve(standard_assumptions(), top_k=5)
        scores = [p.score for p in plans]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual([p.rank for p in plans], [1, 2, 3, 4, 5])

    def test_ties_break_by_coverage_then_cost_then_canonical_key(self) -> None:
        # 两个完全同构、同分同成本的工程，仅编码不同 -> 触发并列
        a = Project("AAA", "A", "c", "r", (Phase(0, 5.0, coverage=2.0,
                                                  industrial=3.0),))
        b = Project("BBB", "B", "c", "r", (Phase(0, 5.0, coverage=2.0,
                                                  industrial=3.0),))
        ass = AssumptionSet("tie", (a, b),
                            Constraints(annual_budget={0: 100.0}), 1)
        plans = solve(ass, top_k=4)
        # 并列时方案 [AAA,BBB] 第一；随后两个单选方案按规范键 AAA 先于 BBB
        keys = [p.canonical_key() for p in plans]
        self.assertEqual(keys[0], "AAA:1|BBB:1")
        self.assertIn("AAA:1|BBB:0", keys)
        self.assertIn("AAA:0|BBB:1", keys)
        # 同分时按规范键升序：'AAA:0...' 字典序先于 'AAA:1...'
        self.assertLess(keys.index("AAA:0|BBB:1"), keys.index("AAA:1|BBB:0"))

    def test_order_independent_of_project_input_order(self) -> None:
        ass1 = standard_assumptions()
        ass2 = AssumptionSet(
            "v1",
            tuple(reversed(ass1.projects)),
            ass1.constraints, ass1.horizon,
            weight_industrial=ass1.weight_industrial,
            weight_coverage=ass1.weight_coverage,
            scenarios=ass1.scenarios,
        )
        self.assertEqual(ass1.digest(), ass2.digest())
        p1 = solve(ass1, top_k=5)
        p2 = solve(ass2, top_k=5)
        self.assertEqual(
            [p.canonical_key() for p in p1],
            [p.canonical_key() for p in p2],
        )
        self.assertEqual([p.score for p in p1], [p.score for p in p2])

    def test_plan_digests_are_stable(self) -> None:
        first = solve(standard_assumptions(), top_k=3)
        second = solve(standard_assumptions(), top_k=3)
        self.assertEqual(
            [p.plan_digest for p in first],
            [p.plan_digest for p in second],
        )

    def test_partial_prefix_is_distinct_option(self) -> None:
        ass = standard_assumptions()
        plans = solve(ass, top_k=10)
        keys = {p.canonical_key() for p in plans}
        # 移动增强仅做一期是独立方案
        self.assertTrue(any("MOBILE:1" in k for k in keys))

    def test_item_reasons_cover_every_selection(self) -> None:
        plan = solve(standard_assumptions(), top_k=1)[0]
        self.assertEqual(
            sorted(r.code for r in plan.reasons),
            sorted(plan.selected_codes),
        )
        for reason in plan.reasons:
            self.assertTrue(reason.rationale)
            self.assertGreaterEqual(reason.prefix, 1)


if __name__ == "__main__":
    unittest.main()
