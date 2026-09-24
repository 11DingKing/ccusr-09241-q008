"""循环依赖与前置校验测试。"""

import unittest

from network_portfolio.domain import (
    AssumptionSet,
    Constraints,
    CyclicDependencyError,
    Phase,
    Project,
)


def _proj(code: str, deps=frozenset()) -> Project:
    return Project(
        code=code, name=code, category="c", region="r",
        phases=(Phase(0, 1.0),), depends_on=deps,
    )


class CyclicDependencyTests(unittest.TestCase):
    def test_direct_cycle_is_rejected(self) -> None:
        a = _proj("A", frozenset({"B"}))
        b = _proj("B", frozenset({"A"}))
        with self.assertRaises(CyclicDependencyError) as ctx:
            AssumptionSet("cyc", (a, b), Constraints({0: 100.0}), 1)
        self.assertEqual(ctx.exception.cycle[0], ctx.exception.cycle[-1])

    def test_three_node_cycle_is_rejected(self) -> None:
        a = _proj("A", frozenset({"C"}))
        b = _proj("B", frozenset({"A"}))
        c = _proj("C", frozenset({"B"}))
        with self.assertRaises(CyclicDependencyError):
            AssumptionSet("cyc", (a, b, c), Constraints({0: 100.0}), 1)

    def test_self_dependency_is_rejected(self) -> None:
        a = _proj("A", frozenset({"A"}))
        with self.assertRaises(CyclicDependencyError):
            AssumptionSet("self", (a,), Constraints({0: 100.0}), 1)

    def test_acyclic_dag_is_accepted(self) -> None:
        a = _proj("A")
        b = _proj("B", frozenset({"A"}))
        c = _proj("C", frozenset({"A"}))
        d = _proj("D", frozenset({"B", "C"}))
        ass = AssumptionSet("dag", (a, b, c, d), Constraints({0: 100.0}), 1)
        self.assertEqual(len(ass.projects), 4)

    def test_mutual_exclusion_is_not_a_dependency_cycle(self) -> None:
        # 互斥不应参与环检测
        a = Project("A", "A", "c", "r", (Phase(0, 1.0),),
                    excludes=frozenset({"B"}))
        b = Project("B", "B", "c", "r", (Phase(0, 1.0),),
                    excludes=frozenset({"A"}))
        ass = AssumptionSet("mutex", (a, b), Constraints({0: 100.0}), 1)
        self.assertEqual(ass.digest() == ass.digest(), True)

    def test_unknown_dependency_reference_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AssumptionSet("x", (_proj("A", frozenset({"GHOST"})),),
                          Constraints({0: 1.0}), 1)


if __name__ == "__main__":
    unittest.main()
