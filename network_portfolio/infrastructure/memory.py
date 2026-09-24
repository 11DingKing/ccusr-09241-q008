"""基础设施适配：内存版仓储与可替换的时钟、标识生成器。"""

from __future__ import annotations

from ..application.ports import (
    AssumptionRepository,
    Clock,
    CommitmentRepository,
    IdGenerator,
    PlanBatchRepository,
    SensitivityReportRepository,
)
from ..application.services import PortfolioService
from ..domain.errors import ImmutableVersionError, NotFoundError, ValidationError
from ..domain.models import AssumptionSet, Commitment, PlanBatch, SensitivityReport


class FixedClock(Clock):
    """返回固定时刻的时钟，保证运行可复现。"""

    def __init__(self, moment: str = "2026-01-01T00:00:00Z") -> None:
        self._moment = moment

    def now(self) -> str:
        return self._moment


class SequentialIds(IdGenerator):
    """按前缀顺序编号，保证同一运行序列内标识确定。"""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def next(self, prefix: str) -> str:
        number = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = number
        return f"{prefix}-{number:04d}"


class InMemoryAssumptionRepository(AssumptionRepository):
    """假设版本只增不改：写入已存在的版本号即拒绝。"""

    def __init__(self) -> None:
        self._versions: dict[int, AssumptionSet] = {}

    def add(self, assumptions: AssumptionSet) -> None:
        if assumptions.version in self._versions:
            raise ImmutableVersionError(f"假设版本 {assumptions.version} 已存在，历史版本不可改写")
        self._versions[assumptions.version] = assumptions

    def get(self, version: int) -> AssumptionSet:
        try:
            return self._versions[version]
        except KeyError:
            raise NotFoundError(f"假设版本不存在: {version}") from None

    def next_version(self) -> int:
        return max(self._versions, default=0) + 1

    def versions(self) -> tuple[int, ...]:
        return tuple(sorted(self._versions))


class InMemoryPlanBatchRepository(PlanBatchRepository):
    def __init__(self) -> None:
        self._batches: dict[str, PlanBatch] = {}

    def add(self, batch: PlanBatch) -> None:
        if batch.batch_id in self._batches:
            raise ValidationError(f"批次标识重复: {batch.batch_id}")
        self._batches[batch.batch_id] = batch

    def get(self, batch_id: str) -> PlanBatch:
        try:
            return self._batches[batch_id]
        except KeyError:
            raise NotFoundError(f"方案批次不存在: {batch_id}") from None


class InMemoryCommitmentRepository(CommitmentRepository):
    def __init__(self) -> None:
        self._commitments: list[Commitment] = []

    def add(self, commitment: Commitment) -> None:
        self._commitments.append(commitment)

    def all(self) -> tuple[Commitment, ...]:
        return tuple(self._commitments)


class InMemorySensitivityReportRepository(SensitivityReportRepository):
    def __init__(self) -> None:
        self._reports: dict[str, SensitivityReport] = {}

    def add(self, report: SensitivityReport) -> None:
        if report.report_id in self._reports:
            raise ValidationError(f"报告标识重复: {report.report_id}")
        self._reports[report.report_id] = report

    def get(self, report_id: str) -> SensitivityReport:
        try:
            return self._reports[report_id]
        except KeyError:
            raise NotFoundError(f"敏感性报告不存在: {report_id}") from None


def build_service(clock: Clock | None = None, ids: IdGenerator | None = None) -> PortfolioService:
    """以内存适配器装配完整服务，适合测试与本地运行。"""
    return PortfolioService(
        assumptions=InMemoryAssumptionRepository(),
        batches=InMemoryPlanBatchRepository(),
        commitments=InMemoryCommitmentRepository(),
        reports=InMemorySensitivityReportRepository(),
        clock=clock or FixedClock(),
        ids=ids or SequentialIds(),
    )
