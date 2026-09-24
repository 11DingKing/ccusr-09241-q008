"""组合求解：约束生效、共享收益去重、并列解与稳定排序。"""

import unittest
from decimal import Decimal

from helpers import make_service, mini_payload, project, sample_payload, stage

from network_portfolio.domain.errors import SearchSpaceTooLargeError


class SamplePortfolioTests(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        self.assumptions = self.service.create_assumptions(sample_payload())

    def test_generates_ranked_plans_with_rationale(self):
        batch = self.service.generate_plans(assumption_version=1, max_plans=3)
        self.assertTrue(batch.feasible)
        self.assertEqual([p.rank for p in batch.plans], [1, 2, 3])
        # 样例数据下最优方案只建设卫星补盲一期
        top = batch.plans[0]
        self.assertEqual(top.selection, {"SAT-FILL": 1})
        self.assertEqual(top.metrics.total_cost, Decimal("80"))
        self.assertEqual(top.metrics.coverage, Decimal("90"))
        # 净值 = (2×90 + 1×10)×0.6 − 80 = 34
        self.assertEqual(top.metrics.net_value, Decimal("114") - Decimal("80"))
        for item in top.items:
            self.assertTrue(item.rationale, "每个入选工程都必须给出入选理由")
        self.assertTrue(any("边际净贡献" in line for line in top.items[0].rationale))
        self.assertTrue(any("最低要求" in line for line in top.items[0].rationale))
        # 未入选工程也有一句话说明
        self.assertEqual({pid for pid, _ in top.excluded_notes}, {"MOB-ENH", "OPT-10G", "COMPUTE"})

    def test_ranking_is_stable_across_runs(self):
        first = self.service.generate_plans(assumption_version=1, max_plans=4)
        second = self.service.generate_plans(assumption_version=1, max_plans=4)
        self.assertEqual(
            [(p.rank, p.selection, p.metrics.net_value) for p in first.plans],
            [(p.rank, p.selection, p.metrics.net_value) for p in second.plans],
        )

    def test_annual_budget_blocks_over_budget_projects(self):
        payload = sample_payload()
        payload["constraints"]["annual_budget"]["2027"] = "120"  # 万兆光网一期 150 超预算
        service = make_service()
        service.create_assumptions(payload)
        batch = service.generate_plans(assumption_version=1, max_plans=8)
        self.assertTrue(batch.feasible)
        for plan in batch.plans:
            self.assertNotIn("OPT-10G", plan.selection)
            self.assertNotIn("COMPUTE", plan.selection)  # 前置工程无法建设，依赖者同样不能入选

    def test_min_coverage_unreachable_yields_diagnostics(self):
        payload = sample_payload()
        payload["constraints"]["min_coverage"] = "100000"
        service = make_service()
        service.create_assumptions(payload)
        batch = service.generate_plans(assumption_version=1)
        self.assertFalse(batch.feasible)
        self.assertEqual(batch.plans, ())
        self.assertTrue(any("普惠覆盖" in message for message in batch.diagnostics))

    def test_search_space_guard(self):
        with self.assertRaises(SearchSpaceTooLargeError):
            self.service.generate_plans(assumption_version=1, max_nodes=5)


class ConstraintTests(unittest.TestCase):
    def test_mutual_exclusion(self):
        service = make_service()
        service.create_assumptions(
            mini_payload(
                [
                    project("A", stages=[stage("一期", 2027, "10", "20")]),
                    project("B", stages=[stage("一期", 2027, "10", "15")]),
                ],
                budget={2027: "1000"},
                mutex=[["A", "B"]],
            )
        )
        batch = service.generate_plans(assumption_version=1, max_plans=5)
        selections = [plan.selection for plan in batch.plans]
        self.assertEqual(batch.plans[0].selection, {"A": 1})  # 价值更高者居首
        self.assertIn({"B": 1}, selections)
        for selection in selections:
            self.assertFalse("A" in selection and "B" in selection)

    def test_prerequisite_forces_full_prereq_build(self):
        service = make_service()
        service.create_assumptions(
            mini_payload(
                [
                    project("A", stages=[stage("一期", 2027, "10", "100")]),
                    project(
                        "B",
                        stages=[
                            stage("一期", 2027, "20", "3"),
                            stage("二期", 2027, "20", "2"),
                        ],
                    ),
                ],
                budget={2027: "1000"},
                prereqs=[["A", "B"]],
            )
        )
        batch = service.generate_plans(assumption_version=1, max_plans=3)
        top = batch.plans[0]
        self.assertEqual(top.selection, {"A": 1, "B": 2})  # 前置工程必须完整建成
        item_b = next(item for item in top.items if item.project_id == "B")
        self.assertTrue(any("前置工程" in line for line in item_b.rationale))

    def test_shared_benefit_counted_once(self):
        service = make_service()
        service.create_assumptions(
            mini_payload(
                [
                    project("A", stages=[stage("一期", 2027, "10", "100")]),
                    project("B", stages=[stage("一期", 2027, "10", "100")]),
                ],
                budget={2027: "1000"},
                shared=[
                    {
                        "group_id": "G1",
                        "project_ids": ["A", "B"],
                        "coverage_overlap": "60",
                        "industry_overlap": "0",
                    }
                ],
            )
        )
        batch = service.generate_plans(assumption_version=1, max_plans=3)
        top = batch.plans[0]
        self.assertEqual(top.selection, {"A": 1, "B": 1})
        self.assertEqual(top.metrics.coverage, Decimal("140"))  # 200 − 60，重复部分只计一次
        self.assertEqual(top.metrics.net_value, Decimal("120"))  # 140 − 20
        for item in top.items:
            self.assertTrue(any("共享收益" in line for line in item.rationale))

    def test_regional_cap_respected(self):
        service = make_service()
        service.create_assumptions(
            mini_payload(
                [
                    project("A", region="东部", stages=[stage("一期", 2027, "50", "80")]),
                    project("B", region="东部", stages=[stage("一期", 2027, "50", "60")]),
                ],
                budget={2027: "1000"},
                caps={"东部": "60"},
            )
        )
        batch = service.generate_plans(assumption_version=1, max_plans=5)
        for plan in batch.plans:
            region_cost = dict(plan.metrics.cost_by_region).get("东部", Decimal("0"))
            self.assertLessEqual(region_cost, Decimal("60"))
        self.assertEqual(batch.plans[0].selection, {"A": 1})


class TiedPlansTests(unittest.TestCase):
    """并列解：净值相同的方案名次相同、标记并列、顺序确定。"""

    def setUp(self):
        self.service = make_service()
        self.service.create_assumptions(
            mini_payload(
                [
                    project("X", stages=[stage("一期", 2027, "10", "30")]),
                    project("Y", stages=[stage("一期", 2027, "10", "30")]),
                ],
                budget={2027: "15"},  # 预算只够二选一
            )
        )

    def test_tied_plans_share_rank_and_are_flagged(self):
        batch = self.service.generate_plans(assumption_version=1, max_plans=1)
        # 边界并列一并保留：虽然只要 1 个方案，两个并列最优都返回
        self.assertEqual(len(batch.plans), 2)
        self.assertEqual([plan.rank for plan in batch.plans], [1, 1])
        self.assertTrue(all(plan.tied for plan in batch.plans))
        self.assertEqual(
            {plan.metrics.net_value for plan in batch.plans},
            {Decimal("20")},
        )

    def test_tied_order_is_deterministic(self):
        first = self.service.generate_plans(assumption_version=1, max_plans=1)
        second = self.service.generate_plans(assumption_version=1, max_plans=1)
        self.assertEqual(
            [plan.selection for plan in first.plans],
            [plan.selection for plan in second.plans],
        )
        # 字典序稳定：X 方案排在 Y 方案之前
        self.assertEqual(first.plans[0].selection, {"X": 1})
        self.assertEqual(first.plans[1].selection, {"Y": 1})


if __name__ == "__main__":
    unittest.main()
