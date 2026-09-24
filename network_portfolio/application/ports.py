"""可替换端口：时间与标识生成，保证业务过程可复现。"""

from __future__ import annotations

from typing import Protocol


class Clock(Protocol):
    def now(self) -> str:
        """返回当前时间的 ISO-8601 字符串。"""


class IdGenerator(Protocol):
    def next_id(self, kind: str) -> str:
        """生成给定实体类型的唯一标识。"""


class SequentialIdGenerator:
    """确定性标识生成器：``batch-1``、``batch-2``……供测试与复现使用。"""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def next_id(self, kind: str) -> str:
        n = self._counters.get(kind, 0) + 1
        self._counters[kind] = n
        return f"{kind}-{n}"


class FixedClock:
    """固定时钟：测试中冻结时间。"""

    def __init__(self, instant: str) -> None:
        self._instant = instant

    def now(self) -> str:
        return self._instant


class SystemClock:
    def now(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat(timespec="seconds")
