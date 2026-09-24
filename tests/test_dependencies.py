"""循环前置依赖的检测与拓扑排序。"""

import unittest

from helpers import make_service, mini_payload, project, stage

from network_portfolio.domain.dependencies import (
    find_cycle,
    prerequisite_closure,
    topological_order,
)
from network_portfolio.domain.errors import CyclicDependencyError


class DependencyGraphTests(unittest.TestCase):
    def test_find_cycle_returns_none_for_dag(self):
        self.assertIsNone(find_cycle(["A", "B", "C"], [("A", "B"), ("B", "C")]))

    def test_find_cycle_reports_cycle_path(self):
        cycle = find_cycle(["A", "B", "C"], [("A", "B"), ("B", "C"), ("C", "A")])
        self.assertIsNotNone(cycle)
        self.assertEqual(cycle[0], cycle[-1])
        self.assertEqual(set(cycle), {"A", "B", "C"})

    def test_topological_order_puts_prerequisites_first(self):
        order = topological_order(["A", "B", "C"], [("A", "B"), ("B", "C")])
        self.assertEqual(order, ["C", "B", "A"])

    def test_topological_order_raises_on_cycle(self):
        with self.assertRaises(CyclicDependencyError):
            topological_order(["A", "B"], [("A", "B"), ("B", "A")])

    def test_prerequisite_closure_is_transitive(self):
        closure = prerequisite_closure([("A", "B"), ("B", "C"), ("X", "Y")], "A")
        self.assertEqual(closure, frozenset({"B", "C"}))


class CyclicDependencyValidationTests(unittest.TestCase):
    """假设数据入库时即拒绝循环前置依赖。"""

    def _payload_with_prereqs(self, prereqs):
        return mini_payload(
            [
                project("A", stages=[stage("一期", 2027, "10", "10")]),
                project("B", stages=[stage("一期", 2027, "10", "10")]),
                project("C", stages=[stage("一期", 2027, "10", "10")]),
            ],
            budget={2027: "1000"},
            prereqs=prereqs,
        )

    def test_self_loop_rejected(self):
        service = make_service()
        with self.assertRaises(CyclicDependencyError) as ctx:
            service.create_assumptions(self._payload_with_prereqs([["A", "A"]]))
        self.assertIn("A", str(ctx.exception))

    def test_two_node_cycle_rejected(self):
        service = make_service()
        with self.assertRaises(CyclicDependencyError) as ctx:
            service.create_assumptions(self._payload_with_prereqs([["A", "B"], ["B", "A"]]))
        message = str(ctx.exception)
        self.assertIn("A", message)
        self.assertIn("B", message)
        self.assertEqual(set(ctx.exception.cycle), {"A", "B"})

    def test_three_node_cycle_rejected(self):
        service = make_service()
        with self.assertRaises(CyclicDependencyError) as ctx:
            service.create_assumptions(
                self._payload_with_prereqs([["A", "B"], ["B", "C"], ["C", "A"]])
            )
        self.assertEqual(set(ctx.exception.cycle), {"A", "B", "C"})

    def test_valid_dag_accepted(self):
        service = make_service()
        assumptions = service.create_assumptions(self._payload_with_prereqs([["A", "B"], ["B", "C"]]))
        self.assertEqual(assumptions.version, 1)


if __name__ == "__main__":
    unittest.main()
