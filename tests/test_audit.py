"""审计查询：任一数字都能还原到所依据的数据版本并重算校验。"""

import unittest
from decimal import Decimal

from helpers import make_service, sample_payload

from network_portfolio.domain.errors import NotFoundError, ValidationError
from network_portfolio.domain.scenarios import Scenario


class PlanAuditTests(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        self.service.create_assumptions(sample_payload())
        self.batch = self.service.generate_plans(assumption_version=1, max_plans=3)
        self.top = self.batch.plans[0]

    def test_audit_net_value_restores_version_and_inputs(self):
        audit = self.service.audit_plan_metric(
            batch_id=self.batch.batch_id, plan_id=self.top.plan_id, metric="net_value"
        )
        self.assertTrue(audit.verified)
        self.assertEqual(audit.assumption_version, 1)
        self.assertIsNone(audit.scenario_name)
        self.assertEqual(audit.value, Decimal("34"))
        self.assertEqual(audit.recomputed, Decimal("34"))
        labels = [name for name, _ in audit.inputs]
        self.assertIn("权重·覆盖", labels)
        self.assertTrue(any(name.startswith("SAT-FILL") for name in labels))
        self.assertIn("net_value", audit.formula)

    def test_audit_plan_level_cost(self):
        audit = self.service.audit_plan_metric(
            batch_id=self.batch.batch_id, plan_id=self.top.plan_id, metric="total_cost"
        )
        self.assertTrue(audit.verified)
        self.assertEqual(audit.value, Decimal("80"))

    def test_audit_project_contribution(self):
        audit = self.service.audit_plan_metric(
            batch_id=self.batch.batch_id,
            plan_id=self.top.plan_id,
            metric="contribution",
            project_id="SAT-FILL",
        )
        self.assertTrue(audit.verified)
        self.assertEqual(audit.value, Decimal("34"))  # 唯一入选工程，移除后净值为 0

    def test_audit_project_stage_cost(self):
        audit = self.service.audit_plan_metric(
            batch_id=self.batch.batch_id,
            plan_id=self.top.plan_id,
            metric="cost",
            project_id="SAT-FILL",
        )
        self.assertTrue(audit.verified)
        self.assertEqual(audit.value, Decimal("80"))
        self.assertEqual(audit.inputs, (("阶段 一期", "80"),))

    def test_audit_unknown_plan_rejected(self):
        with self.assertRaises(NotFoundError):
            self.service.audit_plan_metric(
                batch_id=self.batch.batch_id, plan_id="plan-9999", metric="net_value"
            )

    def test_audit_unknown_batch_rejected(self):
        with self.assertRaises(NotFoundError):
            self.service.audit_plan_metric(
                batch_id="batch-9999", plan_id=self.top.plan_id, metric="net_value"
            )

    def test_audit_unknown_metric_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.audit_plan_metric(
                batch_id=self.batch.batch_id, plan_id=self.top.plan_id, metric="roi"
            )

    def test_audit_project_not_in_plan_rejected(self):
        with self.assertRaises(NotFoundError):
            self.service.audit_plan_metric(
                batch_id=self.batch.batch_id,
                plan_id=self.top.plan_id,
                metric="cost",
                project_id="OPT-10G",
            )


class SensitivityAuditTests(unittest.TestCase):
    def test_audit_scenario_number(self):
        service = make_service()
        service.create_assumptions(sample_payload())
        batch = service.generate_plans(assumption_version=1, max_plans=2)
        top = batch.plans[0]
        report = service.run_sensitivity(
            batch_id=batch.batch_id,
            scenario=Scenario(name="成本超支", cost_factor=Decimal("1.5")),
        )
        audit = service.audit_report_metric(
            report_id=report.report_id, plan_id=top.plan_id, metric="delta"
        )
        self.assertTrue(audit.verified)
        self.assertEqual(audit.subject, "sensitivity")
        self.assertEqual(audit.scenario_name, "成本超支")
        self.assertEqual(audit.value, Decimal("-40"))


if __name__ == "__main__":
    unittest.main()
