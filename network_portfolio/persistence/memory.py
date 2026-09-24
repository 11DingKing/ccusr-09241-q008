"""内存仓库：用于测试与短时进程。"""

from __future__ import annotations

from ..application.records import (
    AssumptionRecord,
    BatchRecord,
    CommitmentRecord,
)


class InMemoryRepository:
    def __init__(self) -> None:
        self._assumptions: dict[str, AssumptionRecord] = {}
        self._by_digest: dict[str, AssumptionRecord] = {}
        self._commitments: dict[str, list[CommitmentRecord]] = {}
        self._batches: dict[str, BatchRecord] = {}

    def save_assumption(self, record: AssumptionRecord) -> None:
        if record.version_id in self._assumptions:
            raise ValueError(f"假设版本 {record.version_id} 已存在，记录不可改写")
        self._assumptions[record.version_id] = record
        self._by_digest.setdefault(record.digest, record)

    def get_assumption(self, version_id: str) -> AssumptionRecord | None:
        return self._assumptions.get(version_id)

    def find_assumption_by_digest(self, digest: str) -> AssumptionRecord | None:
        return self._by_digest.get(digest)

    def list_assumptions(self) -> list[AssumptionRecord]:
        return [self._assumptions[k] for k in sorted(self._assumptions)]

    def save_commitment(self, record: CommitmentRecord) -> None:
        self._commitments.setdefault(record.version_id, []).append(record)

    def list_commitments(self, version_id: str) -> list[CommitmentRecord]:
        return list(self._commitments.get(version_id, ()))

    def save_batch(self, record: BatchRecord) -> None:
        if record.batch_id in self._batches:
            raise ValueError(f"批处理 {record.batch_id} 已存在，记录不可改写")
        self._batches[record.batch_id] = record

    def get_batch(self, batch_id: str) -> BatchRecord | None:
        return self._batches.get(batch_id)

    def list_batches(self) -> list[BatchRecord]:
        return [self._batches[k] for k in sorted(self._batches)]
