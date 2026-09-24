"""应用层持久化记录：一经写入即不可变。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AssumptionRecord:
    """一版假设的留档记录。

    ``snapshot`` 是自描述的完整数据快照，足以在不依赖任何当前代码数据的
    情况下重建该版本，这是审计可还原性的基础。
    """

    version_id: str
    digest: str
    label: str
    created_at: str
    snapshot: dict[str, Any]
    parent_version_id: str | None = None
    parent_digest: str | None = None
    note: str = ""


@dataclass(frozen=True)
class CommitmentRecord:
    """委员会对某假设版本做出的不可撤销阶段承诺。"""

    decision_id: str
    version_id: str
    created_at: str
    commitments: dict[str, int]
    note: str = ""


@dataclass(frozen=True)
class BatchRecord:
    """一次批处理求解的完整留档：请求、稳定排序方案与敏感性结果。"""

    batch_id: str
    created_at: str
    assumption_version_id: str
    assumption_digest: str
    assumption_label: str
    commitments: dict[str, int]
    top_k: int
    plans: tuple[dict[str, Any], ...]
    sensitivity: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    note: str = ""


@dataclass(frozen=True)
class AuditTrace:
    """对方案中某个数字的审计还原结果。"""

    batch_id: str
    plan_digest: str
    assumption_version_id: str
    assumption_digest: str
    metric: str
    value: float
    # 该数字在假设快照中对应的原始输入片段
    evidence: list[dict[str, Any]]
    formula: str
