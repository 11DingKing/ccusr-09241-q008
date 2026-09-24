"""可替换端口：时间、标识生成与持久化，便于稳定复现业务过程。"""

from __future__ import annotations

from typing import Protocol

from ..domain.models import AssumptionSet, Commitment, PlanBatch, SensitivityReport


class Clock(Protocol):
    def now(self) -> str:
        """返回当前时刻的 ISO 文本。"""
        ...


class IdGenerator(Protocol):
    def next(self, prefix: str) -> str:
        """生成带前缀的唯一标识。"""
        ...


class AssumptionRepository(Protocol):
    """假设版本仓储：只增不改，历史版本不可重写。"""

    def add(self, assumptions: AssumptionSet) -> None: ...
    def get(self, version: int) -> AssumptionSet: ...
    def next_version(self) -> int: ...
    def versions(self) -> tuple[int, ...]: ...


class PlanBatchRepository(Protocol):
    def add(self, batch: PlanBatch) -> None: ...
    def get(self, batch_id: str) -> PlanBatch: ...


class CommitmentRepository(Protocol):
    def add(self, commitment: Commitment) -> None: ...
    def all(self) -> tuple[Commitment, ...]: ...


class SensitivityReportRepository(Protocol):
    def add(self, report: SensitivityReport) -> None: ...
    def get(self, report_id: str) -> SensitivityReport: ...
