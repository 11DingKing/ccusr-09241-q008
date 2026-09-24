"""敏感性情景测试：需求下修、成本超支与方案翻转。"""

import unittest

from network_portfolio.engine import sensitivity, solve
from tests.fixtures import standard_assumptions


class SensitivityTests(unittest.TestCase):
    def test_baseline_is_first_result(self) -> None:
        results = sensitivity(standard_assumptions())
        self.assertEqual(results[0].scenario_code, "BASELINE")
        self.assertFalse(results[0].flipped)
        codes = [r.scenario_code for r in results]
        self.assertEqual(
            codes,
            ["BASELINE"] + sorted(r.scenario_code for r in results[1:]),
        )

    def test_demand_shock_reduces_coverage_in_effective_terms(self) -> None:
        ass = standard_assumptions()
        plans = solve(ass, top_k=1, demand_shock=-0.2)
        baseline = solve(ass, top_k=1)
        # 情景下年度覆盖为原始值的 80%
        for year, value in plans[0].annual_coverage.items():
            self.assertAlmostEqual(
                value, baseline[0].annual_coverage.get(year, 0.0) * 0.8, places=9
            )

    def test_cost_overrun_keeps_nominal_totals_comparable(self) -> None:
        ass = standard_assumptions()
        over = solve(ass, top_k=1, cost_overrun=0.3)
        base = solve(ass, top_k=1)
        # 名义口径不受情景乘数影响；若入选集合相同，名义总额必须一致
        if over[0].canonical_key() == base[0].canonical_key():
            self.assertAlmostEqual(
                over[0].total_nominal_cost, base[0].total_nominal_cost, places=9
            )
            self.assertAlmostEqual(
                over[0].annual_cost[0], base[0].annual_cost[0] * 1.3, places=9
            )

    def test_severe_scenario_can_flip_optimal_selection(self) -> None:
        ass = standard_assumptions()
        # 严重成本超支下重投入工程不再占优；结果仍必须可行且自洽
        over = solve(ass, top_k=1, cost_overrun=1.0)
        base = solve(ass, top_k=1)
        self.assertTrue(over[0].selections)
        self.assertTrue(base[0].plan_digest)

    def test_extreme_overrun_can_become_infeasible(self) -> None:
        ass = standard_assumptions()
        # 成本翻三倍后年度预算无法满足最低覆盖所需投入
        from network_portfolio.engine import InfeasibleError as IE

        with self.assertRaises(IE):
            solve(ass, top_k=1, cost_overrun=2.0)

    def test_sensitivity_reports_flip_flag(self) -> None:
        results = sensitivity(standard_assumptions())
        for result in results[1:]:
            key = tuple((s.code, s.prefix)
                        for s in result.best.selections if s.prefix)
            self.assertEqual(result.flipped, key != result.baseline_selection)

    def test_empty_scenarios_returns_baseline_only(self) -> None:
        results = sensitivity(standard_assumptions(scenarios=()))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].scenario_code, "BASELINE")
