"""前置依赖图的校验、拓扑排序与传递闭包。"""

from __future__ import annotations

from heapq import heappop, heappush, heapify

from .errors import CyclicDependencyError


def build_prereq_map(pairs: tuple[tuple[str, str], ...]) -> dict[str, tuple[str, ...]]:
    """由 (依赖者, 前置) 对构建 依赖者 -> 直接前置元组 的映射。"""
    prereqs: dict[str, list[str]] = {}
    for dependent, prereq in pairs:
        bucket = prereqs.setdefault(dependent, [])
        if prereq not in bucket:
            bucket.append(prereq)
    return {pid: tuple(items) for pid, items in prereqs.items()}


def find_cycle(project_ids: list[str], pairs: tuple[tuple[str, str], ...]) -> list[str] | None:
    """若前置关系成环，返回环上的节点路径（首尾相同）；否则返回 None。"""
    prereqs = build_prereq_map(pairs)
    white, gray, black = 0, 1, 2
    color = {pid: white for pid in project_ids}
    stack: list[str] = []
    for root in sorted(project_ids):
        if color[root] != white:
            continue
        color[root] = gray
        stack.append(root)
        work = [(root, iter(sorted(prereqs.get(root, ()))))]
        while work:
            node, successors = work[-1]
            advanced = False
            for nxt in successors:
                if color.get(nxt, white) == gray:
                    idx = stack.index(nxt)
                    return stack[idx:] + [nxt]
                if color.get(nxt, white) == white:
                    color[nxt] = gray
                    stack.append(nxt)
                    work.append((nxt, iter(sorted(prereqs.get(nxt, ())))))
                    advanced = True
                    break
            if not advanced:
                color[node] = black
                stack.pop()
                work.pop()
    return None


def topological_order(project_ids: list[str], pairs: tuple[tuple[str, str], ...]) -> list[str]:
    """返回确定性的拓扑顺序（前置工程排在依赖者之前）。成环时抛出 CyclicDependencyError。"""
    cycle = find_cycle(project_ids, pairs)
    if cycle:
        raise CyclicDependencyError(cycle)
    prereqs = build_prereq_map(pairs)
    dependents: dict[str, list[str]] = {pid: [] for pid in project_ids}
    indegree = {pid: 0 for pid in project_ids}
    for dependent, pre_list in prereqs.items():
        for pre in set(pre_list):
            dependents.setdefault(pre, []).append(dependent)
            indegree[dependent] = indegree.get(dependent, 0) + 1
    ready = [pid for pid in project_ids if indegree.get(pid, 0) == 0]
    heapify(ready)
    order: list[str] = []
    while ready:
        node = heappop(ready)
        order.append(node)
        for dependent in sorted(dependents.get(node, [])):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heappush(ready, dependent)
    if len(order) != len(project_ids):
        remaining = sorted(pid for pid in project_ids if pid not in set(order))
        raise CyclicDependencyError(remaining + remaining[:1])
    return order


def prerequisite_closure(pairs: tuple[tuple[str, str], ...], project_id: str) -> frozenset[str]:
    """某工程的全部直接与间接前置（不含自身）。"""
    prereqs = build_prereq_map(pairs)
    seen: set[str] = set()
    stack = list(prereqs.get(project_id, ()))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(prereqs.get(current, ()))
    return frozenset(seen)
