"""命令行批处理入口。

用法示例（数据目录须显式指定，不会写入源码目录）：

    python -m network_portfolio.cli register --data assumptions.json --data-dir /tmp/np
    python -m network_portfolio.cli run --version version-1 --data-dir /tmp/np
    python -m network_portfolio.cli audit --batch batch-1 --metric totals.nominal_cost \
        --data-dir /tmp/np
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .application import (
    PortfolioService,
    SequentialIdGenerator,
    SystemClock,
    decode_assumptions,
)
from .interface import audit_number, run_planning_batch
from .persistence import JsonFileRepository


def _build_service(data_dir: str) -> PortfolioService:
    repo = JsonFileRepository(data_dir)
    return PortfolioService(repo, SystemClock(), SequentialIdGenerator())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="network_portfolio",
                                     description="网络建设组合决策批处理")
    parser.add_argument("--data-dir", required=True, help="留档数据目录（须显式指定）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_reg = sub.add_parser("register", help="登记一版假设")
    p_reg.add_argument("--data", required=True, help="假设 JSON 文件")
    p_reg.add_argument("--note", default="")

    p_commit = sub.add_parser("commit", help="登记不可撤销阶段承诺")
    p_commit.add_argument("--version", required=True)
    p_commit.add_argument("--data", required=True,
                          help='承诺 JSON，如 {"SATELLITE": 1}')
    p_commit.add_argument("--note", default="")

    p_run = sub.add_parser("run", help="运行批处理求解")
    p_run.add_argument("--version", required=True)
    p_run.add_argument("--top-k", type=int, default=5)
    p_run.add_argument("--no-sensitivity", action="store_true")
    p_run.add_argument("--note", default="")

    p_audit = sub.add_parser("audit", help="审计还原某个数字")
    p_audit.add_argument("--batch", required=True)
    p_audit.add_argument("--metric", required=True)
    p_audit.add_argument("--rank", type=int, default=1)

    p_list = sub.add_parser("list", help="列出已登记版本与批处理")

    args = parser.parse_args(argv)
    service = _build_service(args.data_dir)

    if args.command == "register":
        payload = json.loads(Path(args.data).read_text(encoding="utf-8"))
        record = service.register_assumptions(
            decode_assumptions(payload), note=args.note
        )
        print(json.dumps({"version_id": record.version_id,
                          "digest": record.digest}, ensure_ascii=False))
        return 0

    if args.command == "commit":
        payload = json.loads(Path(args.data).read_text(encoding="utf-8"))
        record = service.commit(args.version, payload, note=args.note)
        print(json.dumps({"decision_id": record.decision_id}, ensure_ascii=False))
        return 0

    if args.command == "run":
        envelope = run_planning_batch(
            service, args.version, top_k=args.top_k,
            include_sensitivity=not args.no_sensitivity, note=args.note,
        )
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
        return 0

    if args.command == "audit":
        envelope = audit_number(service, args.batch, args.metric,
                                plan_rank=args.rank)
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
        return 0

    if args.command == "list":
        out = {
            "assumptions": [
                {"version_id": r.version_id, "digest": r.digest,
                 "label": r.label, "parent": r.parent_version_id}
                for r in service._repo.list_assumptions()
            ],
            "batches": [
                {"batch_id": r.batch_id, "version_id": r.assumption_version_id}
                for r in service._repo.list_batches()
            ],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
