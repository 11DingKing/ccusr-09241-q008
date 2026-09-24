"""批处理与审计查询接口。

对外只暴露纯数据结构（dict/list），字段顺序固定，适合作为批处理作业的
JSON 载荷；同一输入永远产出同一排序与同一摘要。
"""

from __future__ import annotations

from typing import Any, Mapping

from ..application import PortfolioService
from ..application.records import AuditTrace, BatchRecord


def run_planning_batch(
    service: PortfolioService,
    version_id: str,
    *,
    top_k: int = 5,
    extra_commitments: Mapping[str, int] | None = None,
    include_sensitivity: bool = True,
    note: str = "",
) -> dict[str, Any]:
    """执行一次批处理并返回稳定排序的方案信封。"""

    record = service.run_batch(
        version_id,
        top_k=top_k,
        commitments=extra_commitments,
        include_sensitivity=include_sensitivity,
        note=note,
    )
    return batch_envelope(record)


def batch_envelope(record: BatchRecord) -> dict[str, Any]:
    return {
        "batch_id": record.batch_id,
        "created_at": record.created_at,
        "assumption": {
            "version_id": record.assumption_version_id,
            "digest": record.assumption_digest,
            "label": record.assumption_label,
        },
        "commitments": dict(record.commitments),
        "top_k": record.top_k,
        "plans": list(record.plans),
        "sensitivity": list(record.sensitivity),
        "note": record.note,
    }


def audit_number(service: PortfolioService, batch_id: str, metric: str, *,
                 plan_rank: int = 1) -> dict[str, Any]:
    """审计查询：还原方案中任一数字所依据的版本、原始数据片段与公式。"""

    trace = service.audit(batch_id, metric, plan_rank=plan_rank)
    return audit_envelope(trace)


def audit_envelope(trace: AuditTrace) -> dict[str, Any]:
    return {
        "batch_id": trace.batch_id,
        "plan_digest": trace.plan_digest,
        "metric": trace.metric,
        "value": trace.value,
        "based_on": {
            "assumption_version_id": trace.assumption_version_id,
            "assumption_digest": trace.assumption_digest,
        },
        "formula": trace.formula,
        "evidence": trace.evidence,
    }
