"""分期承诺：不可撤销的阶段承诺在方案生成与版本派生中的行为。"""

import unittest
from decimal import Decimal

from helpers import make_service, mini_payload, project, stage

from network_portfolio.domain.errors import CommitmentConflictError


def commitment_payload():
    """P1 两期（各期覆盖 10、成本 50，退出损失 200）；P2 一期（覆盖 100、成本 60）。"""
    return mini_payload(
        [
            project(
                "P1",
                exit_loss="200",
                stages=[
                    stage("一期", 2027, "50", "10"),
                    stage("二期", 2028, "50", "10"),
                ],
            ),
            project("P2", stages=[stage("一期", 2027, "60", "100")]),
        ],
        budget={2027: "1000", 2028: "1000"},
    )


class StagedCommitmentTests(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        self.service.create_assumptions(commitment_payload())

    def test_without_commitment_project_p1_is_not_built(self):
        batch = self.service.generate_plans(assumption_version=1, max_plans=3)
        self.assertEqual(batch.plans[0].selection, {"P2": 1})

    def test_committed_stages_must_be_included(self):
        self.service.commit_stages(
            assumption_version=1, project_id="P1", stage_names=["一期"], decided_by="投资委员会"
        )
        batch = self.service.generate_plans(assumption_version=1, max_plans=8)
        self.assertTrue(batch.feasible)
        for plan in batch.plans:
            self.assertGreaterEqual(plan.selection.get("P1", 0), 1, "已承诺阶段必须纳入每个方案")
        # 最优方案选择把 P1 续建完成，避免退出损失
        top = batch.plans[0]
        self.assertEqual(top.selection, {"P1": 2, "P2": 1})
        self.assertEqual(top.metrics.exit_penalty, Decimal("0"))
        item_p1 = next(item for item in top.items if item.project_id == "P1")
        self.assertTrue(any("不可撤销" in line for line in item_p1.rationale))

    def test_partial_committed_build_incurs_exit_loss(self):
        self.service.commit_stages(assumption_version=1, project_id="P1", stage_names=["一期"])
        batch = self.service.generate_plans(assumption_version=1, max_plans=8)
        partial = [plan for plan in batch.plans if plan.selection.get("P1") == 1]
        self.assertTrue(partial, "应当存在只建设承诺期、不再续建的方案")
        for plan in partial:
            self.assertEqual(plan.metrics.exit_penalty, Decimal("200"))
            self.assertTrue(any("退出损失" in note for note in plan.notes))

    def test_commitment_survives_assumption_derivation(self):
        self.service.commit_stages(assumption_version=1, project_id="P1", stage_names=["一期"])
        revised = self.service.derive_assumptions(
            base_version=1,
            changes={
                "projects": {
                    "update": [project("P2", stages=[stage("一期", 2027, "70", "100")])]
                }
            },
            change_note="P2 成本更正",
        )
        batch = self.service.generate_plans(assumption_version=revised.version, max_plans=8)
        self.assertTrue(batch.feasible)
        for plan in batch.plans:
            self.assertGreaterEqual(plan.selection.get("P1", 0), 1, "承诺跨假设版本持续有效")
        self.assertEqual(dict(batch.committed_snapshot)["P1"], ("一期",))

    def test_commitment_beyond_budget_yields_diagnostics(self):
        self.service.commit_stages(assumption_version=1, project_id="P1", stage_names=["一期"])
        revised = self.service.derive_assumptions(
            base_version=1,
            changes={
                "constraints": {
                    "annual_budget": {"2027": "40", "2028": "1000"},
                    "min_coverage": "0",
                    "regional_caps": {},
                    "prerequisites": [],
                    "mutex_groups": [],
                }
            },
            change_note="年度预算压减",
        )
        batch = self.service.generate_plans(assumption_version=revised.version)
        self.assertFalse(batch.feasible)
        self.assertTrue(any("承诺" in message for message in batch.diagnostics))

    def test_removing_committed_project_conflicts(self):
        self.service.commit_stages(assumption_version=1, project_id="P1", stage_names=["一期"])
        revised = self.service.derive_assumptions(
            base_version=1,
            changes={"projects": {"remove": ["P1"]}},
            change_note="误删已承诺工程",
        )
        with self.assertRaises(CommitmentConflictError):
            self.service.generate_plans(assumption_version=revised.version)

    def test_commit_unknown_stage_rejected(self):
        with self.assertRaises(Exception) as ctx:
            self.service.commit_stages(assumption_version=1, project_id="P1", stage_names=["三期"])
        self.assertIn("三期", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
