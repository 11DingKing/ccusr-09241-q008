"""批处理接口：端到端流程、错误处理与结果稳定性。"""

import unittest

from helpers import make_service, sample_payload

from network_portfolio.interfaces.batch import BatchAPI


def end_to_end_jobs():
    return [
        {"job_id": "create", "action": "create_assumptions", "payload": sample_payload()},
        {
            "job_id": "commit",
            "action": "commit_stages",
            "payload": {
                "assumption_version": 1,
                "project_id": "MOB-ENH",
                "stage_names": ["一期"],
                "decided_by": "投资委员会",
            },
        },
        {"job_id": "generate", "action": "generate_plans", "payload": {"assumption_version": 1, "max_plans": 3}},
    ]


class BatchInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.api = BatchAPI(make_service())

    def test_end_to_end_flow(self):
        results = self.api.submit(end_to_end_jobs())
        self.assertEqual([r["status"] for r in results], ["ok", "ok", "ok"])
        generated = results[2]["data"]
        self.assertTrue(generated["feasible"])
        # 已承诺的移动增强一期必须出现在每个方案中
        for plan in generated["plans"]:
            selected = {item["project_id"]: item["stages"] for item in plan["items"]}
            self.assertIn("MOB-ENH", selected)
            self.assertIn("一期", selected["MOB-ENH"])
        # 排名稳定且逐项给出入选理由
        ranks = [plan["rank"] for plan in generated["plans"]]
        self.assertEqual(ranks, sorted(ranks))
        for plan in generated["plans"]:
            for item in plan["items"]:
                self.assertTrue(item["rationale"])
        # 基于该批次继续做敏感性与审计
        batch_id = generated["batch_id"]
        top_plan_id = generated["plans"][0]["plan_id"]
        sensitivity = self.api.handle(
            "run_sensitivity",
            {"batch_id": batch_id, "scenario": {"name": "成本超支", "cost_factor": "1.5"}},
        )
        self.assertEqual(sensitivity["status"], "ok")
        report_id = sensitivity["data"]["report_id"]
        audit = self.api.handle(
            "audit", {"report_id": report_id, "plan_id": top_plan_id, "metric": "scenario_net_value"}
        )
        self.assertEqual(audit["status"], "ok")
        self.assertTrue(audit["data"]["verified"])
        self.assertEqual(audit["data"]["assumption_version"], 1)
        self.assertEqual(audit["data"]["scenario_name"], "成本超支")

    def test_derive_and_regenerate_via_interface(self):
        self.api.handle("create_assumptions", sample_payload())
        derived = self.api.handle(
            "derive_assumptions",
            {
                "base_version": 1,
                "changes": {"weights": {"coverage": "3", "industry": "1", "cost": "1"}},
                "change_note": "提高覆盖权重",
            },
        )
        self.assertEqual(derived["status"], "ok")
        self.assertEqual(derived["data"]["version"], 2)
        self.assertEqual(derived["data"]["parent_version"], 1)
        versions = self.api.handle("list_versions")
        self.assertEqual(versions["data"]["versions"], [1, 2])
        regenerated = self.api.handle("generate_plans", {"assumption_version": 2})
        self.assertEqual(regenerated["status"], "ok")
        self.assertEqual(regenerated["data"]["assumption_version"], 2)

    def test_unknown_action_returns_error(self):
        result = self.api.handle("explode", {})
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["type"], "ValidationError")

    def test_missing_field_returns_error(self):
        result = self.api.handle("generate_plans", {})
        self.assertEqual(result["status"], "error")
        self.assertIn("assumption_version", result["error"]["message"])

    def test_business_error_does_not_stop_batch(self):
        jobs = [
            {"action": "generate_plans", "payload": {"assumption_version": 42}},  # 版本不存在
            {"action": "create_assumptions", "payload": sample_payload()},
            {"action": "generate_plans", "payload": {"assumption_version": 1}},
        ]
        results = self.api.submit(jobs)
        self.assertEqual(results[0]["status"], "error")
        self.assertEqual(results[0]["error"]["type"], "NotFoundError")
        self.assertEqual(results[1]["status"], "ok")
        self.assertEqual(results[2]["status"], "ok")

    def test_same_inputs_produce_identical_output(self):
        other = BatchAPI(make_service())
        first = self.api.submit(end_to_end_jobs())
        second = other.submit(end_to_end_jobs())
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
