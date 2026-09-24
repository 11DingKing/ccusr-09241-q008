"""假设更正：版本只增不改，派生修订，历史批次仍可审计还原。"""

import unittest
from decimal import Decimal

from helpers import make_service, sample_payload

from network_portfolio.domain.errors import ImmutableVersionError, NotFoundError
from network_portfolio.infrastructure.memory import InMemoryAssumptionRepository


class AssumptionVersionTests(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        # 初始版本中卫星补盲成本被误录为 800（正确值 80）
        payload = sample_payload()
        sat = next(p for p in payload["projects"] if p["project_id"] == "SAT-FILL")
        sat["stages"][0]["cost"] = "800"
        self.v1 = self.service.create_assumptions(payload)
        self.batch1 = self.service.generate_plans(assumption_version=1, max_plans=3)

    def test_correction_creates_new_version_and_changes_ranking(self):
        corrected = sample_payload()  # 正确成本 80
        v2 = self.service.derive_assumptions(
            base_version=1,
            changes={"projects": {"update": corrected["projects"]}},
            change_note="更正卫星补盲一期成本 800 -> 80",
        )
        self.assertEqual(v2.version, 2)
        self.assertEqual(v2.parent_version, 1)
        self.assertEqual(self.service.list_versions(), (1, 2))
        # 更正前：卫星补盲因成本失真无法入选；更正后：成为最优方案
        self.assertNotIn("SAT-FILL", self.batch1.plans[0].selection)
        batch2 = self.service.generate_plans(assumption_version=2, max_plans=3)
        self.assertEqual(batch2.assumption_version, 2)
        self.assertEqual(batch2.plans[0].selection, {"SAT-FILL": 1})
        self.assertEqual(batch2.plans[0].metrics.net_value, Decimal("34"))

    def test_historical_version_is_not_rewritten(self):
        corrected = sample_payload()
        self.service.derive_assumptions(
            base_version=1, changes={"projects": {"update": corrected["projects"]}}
        )
        # 历史版本数据保持原样
        v1 = self.service.get_assumptions(1)
        sat = next(p for p in v1.projects if p.project_id == "SAT-FILL")
        self.assertEqual(sat.stages[0].cost, Decimal("800"))
        # 历史批次数字保持原样
        batch1 = self.service.get_batch(self.batch1.batch_id)
        self.assertEqual(batch1.plans[0].metrics.net_value, Decimal("-13"))
        # 历史批次仍可审计还原到版本 1
        audit = self.service.audit_plan_metric(
            batch_id=self.batch1.batch_id,
            plan_id=self.batch1.plans[0].plan_id,
            metric="net_value",
        )
        self.assertTrue(audit.verified)
        self.assertEqual(audit.assumption_version, 1)
        self.assertEqual(audit.value, Decimal("-13"))

    def test_duplicate_version_rejected(self):
        repo = InMemoryAssumptionRepository()
        repo.add(self.v1)
        with self.assertRaises(ImmutableVersionError):
            repo.add(self.v1)

    def test_derive_from_missing_version_rejected(self):
        with self.assertRaises(NotFoundError):
            self.service.derive_assumptions(base_version=99, changes={})

    def test_update_unknown_project_rejected(self):
        payload = sample_payload()
        payload["projects"][0]["project_id"] = "NOPE"
        with self.assertRaises(NotFoundError):
            self.service.derive_assumptions(
                base_version=1, changes={"projects": {"update": payload["projects"][:1]}}
            )


if __name__ == "__main__":
    unittest.main()
