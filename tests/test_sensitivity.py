"""敏感性分析：需求下修、成本超支等情景下的方案重估与重新寻优。"""

import unittest
from decimal import Decimal

from helpers import make_service, sample_payload

from network_portfolio.domain.scenarios import Scenario


class SensitivityTests(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        self.service.create_assumptions(sample_payload())
        self.batch = self.service.generate_plans(assumption_version=1, max_plans=3)
        self.top = self.batch.plans[0]  # {"SAT-FILL": 1}，净值 34

    def test_cost_overrun_reduces_net_value(self):
        report = self.service.run_sensitivity(
            batch_id=self.batch.batch_id,
            scenario=Scenario(name="成本超支", cost_factor=Decimal("1.5")),
        )
        result = next(r for r in report.results if r.plan_id == self.top.plan_id)
        self.assertEqual(result.base_net_value, Decimal("34"))
        # 成本 80 -> 120，净值 114 - 120 = -6
        self.assertEqual(result.scenario_net_value, Decimal("-6"))
        self.assertEqual(result.delta, Decimal("-40"))
        self.assertTrue(result.feasible)
        # 情景下重新寻优仍选卫星补盲
        self.assertTrue(report.optimal_feasible)
        self.assertEqual(dict(report.optimal_selection), {"SAT-FILL": 1})

    def test_demand_downgrade_can_break_coverage_constraint(self):
        report = self.service.run_sensitivity(
            batch_id=self.batch.batch_id,
            scenario=Scenario(name="需求下修", benefit_factor=Decimal("0.5")),
        )
        result = next(r for r in report.results if r.plan_id == self.top.plan_id)
        self.assertFalse(result.feasible)
        self.assertTrue(any("普惠覆盖" in v for v in result.violations))
        # 情景下最优组合转向移动增强 + 卫星补盲
        self.assertTrue(report.optimal_feasible)
        self.assertEqual(dict(report.optimal_selection), {"MOB-ENH": 2, "SAT-FILL": 1})
        self.assertEqual(report.optimal_net_value, Decimal("-139.5"))

    def test_extreme_overrun_makes_everything_infeasible(self):
        report = self.service.run_sensitivity(
            batch_id=self.batch.batch_id,
            scenario=Scenario(name="极端超支", cost_factor=Decimal("10")),
        )
        self.assertFalse(report.optimal_feasible)
        self.assertIsNone(report.optimal_net_value)
        self.assertTrue(all(not r.feasible for r in report.results))

    def test_project_level_factor(self):
        report = self.service.run_sensitivity(
            batch_id=self.batch.batch_id,
            scenario=Scenario(
                name="卫星单点超支",
                project_cost_factors=(("SAT-FILL", Decimal("2")),),
            ),
        )
        result = next(r for r in report.results if r.plan_id == self.top.plan_id)
        # 仅卫星补盲成本 80 -> 160
        self.assertEqual(result.scenario_net_value, Decimal("-46"))
        self.assertEqual(result.delta, Decimal("-80"))

    def test_report_is_persisted_and_auditable(self):
        report = self.service.run_sensitivity(
            batch_id=self.batch.batch_id,
            scenario=Scenario(name="成本超支", cost_factor=Decimal("1.5")),
        )
        audit = self.service.audit_report_metric(
            report_id=report.report_id, plan_id=self.top.plan_id, metric="scenario_net_value"
        )
        self.assertTrue(audit.verified)
        self.assertEqual(audit.scenario_name, "成本超支")
        self.assertEqual(audit.assumption_version, 1)


if __name__ == "__main__":
    unittest.main()
