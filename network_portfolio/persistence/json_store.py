"""追加式 JSON 文件仓库。

数据目录必须由调用方显式提供（不得默认落在源码目录）。每条记录写入
独立不可变文件，另维护仅追加的清单索引；写盘采用"临时文件 + 原子替换"。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ..application.records import (
    AssumptionRecord,
    BatchRecord,
    CommitmentRecord,
)


def _record_to_dict(record) -> dict:
    from dataclasses import asdict

    return {"__type__": type(record).__name__, **asdict(record)}


def _record_from_dict(data: dict):
    kind = data["__type__"]
    payload = {k: v for k, v in data.items() if k != "__type__"}
    if kind == "AssumptionRecord":
        return AssumptionRecord(**payload)
    if kind == "CommitmentRecord":
        return CommitmentRecord(**payload)
    if kind == "BatchRecord":
        payload["plans"] = tuple(payload["plans"])
        payload["sensitivity"] = tuple(payload.get("sensitivity", ()))
        return BatchRecord(**payload)
    raise ValueError(f"未知记录类型 {kind}")


class JsonFileRepository:
    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._assumptions_dir = self._dir / "assumptions"
        self._commitments_dir = self._dir / "commitments"
        self._batches_dir = self._dir / "batches"
        for d in (self._assumptions_dir, self._commitments_dir, self._batches_dir):
            d.mkdir(exist_ok=True)

    def _atomic_write_json(self, path: Path, payload: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, sort_keys=True, indent=2)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    @staticmethod
    def _read_json(path: Path) -> dict:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def save_assumption(self, record: AssumptionRecord) -> None:
        path = self._assumptions_dir / f"{record.version_id}.json"
        if path.exists():
            raise ValueError(f"假设版本 {record.version_id} 已存在，记录不可改写")
        self._atomic_write_json(path, _record_to_dict(record))

    def get_assumption(self, version_id: str) -> AssumptionRecord | None:
        path = self._assumptions_dir / f"{version_id}.json"
        return _record_from_dict(self._read_json(path)) if path.exists() else None

    def find_assumption_by_digest(self, digest: str) -> AssumptionRecord | None:
        for path in sorted(self._assumptions_dir.glob("*.json")):
            record = _record_from_dict(self._read_json(path))
            if record.digest == digest:
                return record
        return None

    def list_assumptions(self) -> list[AssumptionRecord]:
        records = [
            _record_from_dict(self._read_json(p))
            for p in self._assumptions_dir.glob("*.json")
        ]
        return sorted(records, key=lambda r: r.version_id)

    def save_commitment(self, record: CommitmentRecord) -> None:
        path = self._commitments_dir / f"{record.decision_id}.json"
        if path.exists():
            raise ValueError(f"承诺 {record.decision_id} 已存在，记录不可改写")
        self._atomic_write_json(path, _record_to_dict(record))

    def list_commitments(self, version_id: str) -> list[CommitmentRecord]:
        records = [
            _record_from_dict(self._read_json(p))
            for p in self._commitments_dir.glob("*.json")
        ]
        return sorted(
            (r for r in records if r.version_id == version_id),
            key=lambda r: r.decision_id,
        )

    def save_batch(self, record: BatchRecord) -> None:
        path = self._batches_dir / f"{record.batch_id}.json"
        if path.exists():
            raise ValueError(f"批处理 {record.batch_id} 已存在，记录不可改写")
        self._atomic_write_json(path, _record_to_dict(record))

    def get_batch(self, batch_id: str) -> BatchRecord | None:
        path = self._batches_dir / f"{batch_id}.json"
        return _record_from_dict(self._read_json(path)) if path.exists() else None

    def list_batches(self) -> list[BatchRecord]:
        records = [
            _record_from_dict(self._read_json(p))
            for p in self._batches_dir.glob("*.json")
        ]
        return sorted(records, key=lambda r: r.batch_id)
