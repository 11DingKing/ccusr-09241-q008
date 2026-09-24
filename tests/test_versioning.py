"""假设版本管理：派生、幂等与历史版本不可变。"""

import unittest

from network_portfolio.application import (
    PortfolioService,
    SequentialIdGenerator,
    FixedClock,
    decode_assumptions,
)
from network_portfolio.domain import Constraints, Phase, Project
from network_portfolio.persistence import InMemoryRepository
from tests.fixtures import standard_assumptions


class VersioningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = PortfolioService(
            InMemoryRepository(),
            FixedClock("2026-09-24T00:00:00+00:00"),
            SequentialIdGenerator(),
        )

    def test_register_assigns_version_and_digest(self) -> None:
        record = self.service.register_assumptions(standard_assumptions())
        self.assertTrue(record.version_id)
        self.assertEqual(len(record.digest), 16)

    def test_identical_content_is_idempotent(self) -> None:
        first = self.service.register_assumptions(standard_assumptions())
        second = self.service.register_assumptions(
            standard_assumptions(label="v1-renamed-content-same-fields-differ-label")
        )
        # label 参与摘要，不同 label 是不同版本
        self.assertNotEqual(first.version_id, second.version_id)
        again = self.service.register_assumptions(standard_assumptions())
        self.assertEqual(again.version_id, first.version_id)

    def test_derive_keeps_parent_unchanged(self) -> None:
        parent = self.service.register_assumptions(standard_assumptions(label="v1"))
        tighter = Constraints(annual_budget={0: 10.0, 1: 10.0},
                              min_annual_coverage={0: 4.0})
        child = self.service.derive_assumptions(
            parent.version_id, label="v2-预算收紧", constraints=tighter
        )
        self.assertNotEqual(child.version_id, parent.version_id)
        self.assertEqual(child.parent_version_id, parent.version_id)
        self.assertEqual(child.parent_digest, parent.digest)
        # 父版本快照原样保留
        reloaded = self.service.load_assumptions(parent.version_id)
        self.assertEqual(reloaded.constraints.annual_budget, {0: 25.0, 1: 20.0})

    def test_derive_with_no_change_returns_parent(self) -> None:
        parent = self.service.register_assumptions(standard_assumptions())
        child = self.service.derive_assumptions(parent.version_id)
        self.assertEqual(child.version_id, parent.version_id)

    def test_assumption_correction_is_new_version(self) -> None:
        # 委员会更正移动增强的成本参数：必须产生新版本，旧版本数字仍可复核
        parent = self.service.register_assumptions(standard_assumptions(label="v1"))
        corrected_projects = []
        for proj in standard_assumptions().projects:
            if proj.code == "MOBILE":
                proj = Project(
                    code=proj.code, name=proj.name, category=proj.category,
                    region=proj.region,
                    phases=tuple(
                        Phase(ph.year, ph.cost + 2.0, ph.coverage,
                              ph.industrial, ph.exit_loss)
                        for ph in proj.phases
                    ),
                    maturity=proj.maturity, depends_on=proj.depends_on,
                    excludes=proj.excludes,
                )
            corrected_projects.append(proj)
        child = self.service.derive_assumptions(
            parent.version_id, label="v2-成本更正",
            projects=tuple(corrected_projects),
        )
        self.assertNotEqual(child.digest, parent.digest)
        old = self.service.load_assumptions(parent.version_id)
        new = self.service.load_assumptions(child.version_id)
        self.assertEqual(
            old.project_map["MOBILE"].phases[0].cost, 10.0
        )
        self.assertEqual(
            new.project_map["MOBILE"].phases[0].cost, 12.0
        )

    def test_snapshot_round_trip_reconstructs_domain(self) -> None:
        ass = standard_assumptions()
        record = self.service.register_assumptions(ass)
        rebuilt = decode_assumptions(record.snapshot)
        self.assertEqual(rebuilt.digest(), ass.digest())

    def test_historical_version_cannot_be_overwritten(self) -> None:
        record = self.service.register_assumptions(standard_assumptions())
        with self.assertRaises(ValueError):
            self.service._repo.save_assumption(record)
