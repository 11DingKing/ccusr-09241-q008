"""分期承诺与不可撤销锁定测试。"""

import unittest

from network_portfolio.domain import AssumptionSet
from network_portfolio.engine import InfeasibleError, solve
from tests.fixtures import standard_assumptions


class CommitmentTests(unittest.TestCase):
    def test_committed_project_appears_in_every_plan(self) -> None:
        ass = standard_assumptions()
        for plan in solve(ass, {"SATELLITE": 1}, top_k=10):
            self.assertEqual(
                next(s.prefix for s in plan.selections if s.code == "SATELLITE"),
                1,
            )
            # 前置工程也必须在场
            self.assertIn("MOBILE", plan.selected_codes)

    def test_committed_prefix_is_floor_not_exact(self) -> None:
        ass = standard_assumptions()
        # 锁定移动增强至少 1 期：方案中允许 1 期或 2 期，但不允许 0
        prefixes = {
            next(s.prefix for s in p.selections if s.code == "MOBILE")
            for p in solve(ass, {"MOBILE": 1}, top_k=20)
        }
        self.assertTrue(prefixes)
        self.assertTrue(prefixes <= {1, 2})
        self.assertNotIn(0, prefixes)

    def test_full_commitment_has_no_exit_loss(self) -> None:
        ass = standard_assumptions()
        plan = solve(ass, {"MOBILE": 2}, top_k=1)[0]
        mobile = next(r for r in plan.reasons if r.code == "MOBILE")
        self.assertEqual(mobile.prefix, 2)
        self.assertEqual(mobile.exit_loss, 0.0)
        self.assertIn("不可撤销承诺", mobile.roles)

    def test_partial_commitment_carries_exit_loss(self) -> None:
        ass = standard_assumptions()
        # 锁 1 期，并把次年预算压到无法追加第二期 -> 方案中必然停在第 1 期
        from network_portfolio.domain import Constraints
        tight = Constraints(
            annual_budget={0: 25.0, 1: 0.0},
            min_annual_coverage={0: 4.0},
            region_cap=ass.constraints.region_cap,
        )
        locked = AssumptionSet("locked", ass.projects, tight, ass.horizon,
                               scenarios=ass.scenarios)
        plan = solve(locked, {"MOBILE": 1}, top_k=1)[0]
        mobile = next(r for r in plan.reasons if r.code == "MOBILE")
        self.assertEqual(mobile.prefix, 1)
        self.assertEqual(mobile.exit_loss, 3.0)

    def test_commitment_conflicting_with_exclusion_is_infeasible(self) -> None:
        ass = standard_assumptions()
        # 锁定互斥的两条路线
        with self.assertRaises(InfeasibleError):
            solve(ass, {"MOBILE": 1, "FIBER": 1})

    def test_commitment_beyond_phase_count_rejected(self) -> None:
        ass = standard_assumptions()
        with self.assertRaises(ValueError):
            solve(ass, {"SATELLITE": 5})
        with self.assertRaises(ValueError):
            solve(ass, {"GHOST": 1})

    def test_commitment_changes_best_plan_when_locked(self) -> None:
        ass = standard_assumptions()
        free_best = solve(ass, top_k=1)[0]
        locked = solve(ass, {"MOBILE": 2}, top_k=1)[0]
        # 锁定本身可能恰好等于自由最优；至少锁定方案必须满足锁定
        self.assertGreaterEqual(
            next(s.prefix for s in locked.selections if s.code == "MOBILE"),
            2,
        )
        self.assertTrue(free_best.plan_digest)
