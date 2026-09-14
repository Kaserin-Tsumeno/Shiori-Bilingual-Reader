from __future__ import annotations
import sys as _sys
from pathlib import Path as _KitPath

_sys.path.insert(0, str(_KitPath(__file__).resolve().parent))
import kit_config as cfg

r"""续跑进度看板：统计哪些工作单元已产出、哪些还缺。

判定标准：parts/unit_XXXX.jsonl（或 unit_XXXX.partX.jsonl 片段）中出现的 id
是否覆盖该单元的原文 id 全集。

用法：
  py -3.11 tools\unit_progress.py --work-id my-novel
  # 加 --list-pending 列出待办单元号（适合直接安排下一批生产）
"""

import argparse
import json
from pathlib import Path


def load_ids(path: Path) -> list[str]:
    ids: list[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ids.append(str(json.loads(line)["id"]))
            except Exception:
                pass
    return ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=None, help="工作根目录，默认 KIT_ROOT 或仓库根")
    parser.add_argument("--work-id", default=None, help="作品 id（也可用环境变量 WORK_ID）")
    parser.add_argument("--list-pending", action="store_true")
    parser.add_argument("--pending-limit", type=int, default=40)
    args = parser.parse_args()

    work_id = cfg.resolve_work_id(args.work_id)
    units_dir = cfg.units_dir(work_id)
    parts_dir = cfg.parts_dir(work_id)

    produced: set[str] = set()
    if parts_dir.exists():
        for path in sorted(parts_dir.glob("*.jsonl")):
            produced.update(load_ids(path))

    done_units: list[int] = []
    pending_units: list[dict] = []
    total_paras = 0
    done_paras = 0

    for path in sorted(units_dir.glob("unit_*.jsonl")):
        num = int(path.stem.split("_")[1])
        ids = [str(r["id"]) for r in (json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip())]
        total_paras += len(ids)
        missing = [i for i in ids if i not in produced]
        done_paras += len(ids) - len(missing)
        if not missing:
            done_units.append(num)
        else:
            pending_units.append(
                {"unit": num, "first_id": ids[0], "last_id": ids[-1], "missing": len(missing)}
            )

    report = {
        "total_units": len(done_units) + len(pending_units),
        "done_units": len(done_units),
        "pending_units": len(pending_units),
        "total_paragraphs": total_paras,
        "produced_paragraphs": done_paras,
        "progress_percent": round(done_paras / total_paras * 100, 2) if total_paras else 0.0,
    }
    if args.list_pending:
        report["next_pending"] = pending_units[: args.pending_limit]
        report["next_pending_unit_numbers"] = [u["unit"] for u in pending_units[: args.pending_limit]]
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
