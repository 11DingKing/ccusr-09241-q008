"""批处理接口与审计还原测试。"""

import json
import unittest

from network_portfolio.application import (
    PortfolioService,
    SequentialIdGenerator,
    FixedClock,
)
from network_portfolio.interface import audit_number, run_planning_batch
from network_portfolio.persistence import InMemoryRepository, JsonFileRepository
from tests.fixtures import standard_assumptions


def _service(repo=None):
    return PortfolioService(
        repo or InMemoryRepository(),
        FixedClock("2026-09-24T00:00:00+00:00"),
        SequentialIdGenerator(),
    )


class BatchInterfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _service()
        self.version = self.service.register_assumptions(
            standard_assumptions(label="v1"), note="委员会基线"
        )

    def test_batch_envelope_is_json_serializable_and_sorted(self) -> None:
        env = run_planning_batch(self.service, self.version.version_id, top_k=3)
        blob = json.dumps(env, ensure_ascii=False)
        self.assertIn("batch-1", blob)
        ranks = [p["rank"] for p in env["plans"]]
        self.assertEqual(ranks, [1, 2, 3])
        scores = [p["score"] for p in env["plans"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_batch_is_reproducible(self) -> None:
        env1 = run_planning_batch(self.service, self.version.version_id, top_k=3)
        env2 = run_planning_batch(self.service, self.version.version_id, top_k=3)
        self.assertEqual(
            [p["plan_digest"] for p in env1["plans"]],
            [p["plan_digest"] for p in env2["plans"]],
        )

    def test_batch_includes_per_item_reasons(self) -> None:
        env = run_planning_batch(self.service, self.version.version_id, top_k=1)
        plan = env["plans"][0]
        selected = [s["code"] for s in plan["selections"] if s["selected"]]
        reason_codes = [r["code"] for r in plan["reasons"]]
        self.assertEqual(sorted(selected), sorted(reason_codes))
        for reason in plan["reasons"]:
            self.assertTrue(reason["rationale"])

    def test_batch_includes_sensitivity_results(self) -> None:
        env = run_planning_batch(self.service, self.version.version_id, top_k=2)
        codes = [s["scenario_code"] for s in env["sensitivity"]]
        self.assertEqual(codes[0], "BASELINE")
        self.assertIn("DEMAND_DOWN", codes)
        self.assertIn("COST_OVERRUN", codes)

    def test_batch_after_commitment_carries_lock(self) -> None:
        self.service.commit(self.version.version_id, {"MOBILE": 2},
                            note="委员会一期决议")
        env = run_planning_batch(self.service, self.version.version_id, top_k=3)
        self.assertEqual(env["commitments"].get("MOBILE"), 2)
        for plan in env["plans"]:
            mobile = next(s for s in plan["selections"] if s["code"] == "MOBILE")
            self.assertGreaterEqual(mobile["prefix"], 2)

    def test_batch_references_exact_assumption_version(self) -> None:
        env = run_planning_batch(self.service, self.version.version_id)
        self.assertEqual(
            env["assumption"]["digest"], self.version.digest
        )


class AuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _service()
        self.version = self.service.register_assumptions(standard_assumptions())
        self.env = run_planning_batch(self.service, self.version.version_id,
                                      top_k=3)
        self.batch_id = self.env["batch_id"]

    def test_audit_total_cost_matches_plan(self) -> None:
        trace = audit_number(self.service, self.batch_id, "totals.nominal_cost")
        plan_value = self.env["plans"][0]["totals"]["nominal_cost"]
        self.assertAlmostEqual(trace["value"], plan_value, places=9)
        self.assertTrue(trace["evidence"])
        self.assertEqual(
            trace["based_on"]["assumption_digest"], self.version.digest
        )

    def test_audit_evidence_rows_cite_raw_phase_inputs(self) -> None:
        trace = audit_number(self.service, self.batch_id, "totals.nominal_cost")
        for row in trace["evidence"]:
            self.assertIn("raw_cost", row)
            self.assertIn("year", row)
            self.assertIn("project", row)

    def test_audit_score_reproduces_plan_score(self) -> None:
        trace = audit_number(self.service, self.batch_id, "score")
        self.assertAlmostEqual(
            trace["value"], self.env["plans"][0]["score"], places=9
        )

    def test_audit_annual_cost_uses_cost_multiplier(self) -> None:
        trace = audit_number(self.service, self.batch_id, "annual_cost.0")
        expected = sum(
            row["effective_cost"] for row in trace["evidence"]
        )
        self.assertAlmostEqual(trace["value"], expected, places=9)
        self.assertEqual(
            self.env["plans"][0]["annual_cost"].get("0", 0.0), trace["value"]
        )

    def test_audit_item_level_metric(self) -> None:
        code = self.env["plans"][0]["reasons"][0]["code"]
        trace = audit_number(
            self.service, self.batch_id, f"item.{code}.coverage"
        )
        reason = next(r for r in self.env["plans"][0]["reasons"]
                      if r["code"] == code)
        self.assertAlmostEqual(trace["value"], reason["coverage"], places=9)

    def test_audit_second_rank_plan(self) -> None:
        trace = audit_number(self.service, self.batch_id,
                             "totals.nominal_cost", plan_rank=2)
        self.assertAlmostEqual(
            trace["value"],
            self.env["plans"][1]["totals"]["nominal_cost"], places=9,
        )

    def test_audit_unknown_metric_raises(self) -> None:
        with self.assertRaises(KeyError):
            audit_number(self.service, self.batch_id, "nonsense")
        with self.assertRaises(KeyError):
            audit_number(self.service, "missing-batch", "score")

    def test_audit_still_resolves_after_new_version_registered(self) -> None:
        # 登记新版本后，历史批处理引用的数字仍由旧版本快照还原
        self.service.derive_assumptions(
            self.version.version_id, label="v2",
            weight_coverage=5.0,
        )
        trace = audit_number(self.service, self.batch_id, "score")
        self.assertEqual(
            trace["based_on"]["assumption_digest"], self.version.digest
        )
        self.assertAlmostEqual(
            trace["value"], self.env["plans"][0]["score"], places=9
        )


class JsonFileRepositoryTests(unittest.TestCase):
    def test_persistence_roundtrip_and_audit(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "ledger"
            service = _service(JsonFileRepository(data_dir))
            version = service.register_assumptions(standard_assumptions())
            env = run_planning_batch(service, version.version_id, top_k=2)
            service.commit(version.version_id, {"MOBILE": 1})

            # 用全新仓库实例重新打开同一目录
            reopened = _service(JsonFileRepository(data_dir))
            versions = reopened._repo.list_assumptions()
            self.assertEqual(len(versions), 1)
            batches = reopened._repo.list_batches()
            self.assertEqual(len(batches), 1)
            decisions = reopened._repo.list_commitments(version.version_id)
            self.assertEqual(decisions[0].commitments, {"MOBILE": 1})
            trace = audit_number(reopened, env["batch_id"], "score")
            self.assertAlmostEqual(
                trace["value"], env["plans"][0]["score"], places=9
            )

    def test_records_are_immutable_on_disk(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            service = _service(JsonFileRepository(tmp))
            version = service.register_assumptions(standard_assumptions())
            with self.assertRaises(ValueError):
                service._repo.save_assumption(version)


if __name__ == "__main__":
    unittest.main()
