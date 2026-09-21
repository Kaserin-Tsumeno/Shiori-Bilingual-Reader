from __future__ import annotations
import sys as _sys
from pathlib import Path as _ShioriPath

_sys.path.insert(0, str(_ShioriPath(__file__).resolve().parent))
import shiori_config as cfg

"""按段落区间生成续跑分块（默认 600 段一块）。

从 chunks/<work_id>/*.jsonl（原始 80 段小块）中提取 p%05d 起连续的原文段落，
重新切成固定大小的分块，输出到 chunks_600/<work_id>/chunk_600_%04d.jsonl。

只搬运原文字段，不修改 ja 内容；输出为 UTF-8 无 BOM，\n 换行。
"""

import argparse
import json
import re
from pathlib import Path

ID_RE = re.compile(r"^p(\d+)$")


def load_source_rows(src_dir: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    order: list[str] = []
    for path in sorted(src_dir.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                pid = str(row["id"])
                if pid in rows:
                    raise SystemExit(f"duplicate id in source: {pid} ({path.name})")
                rows[pid] = row
                order.append(pid)
    rows["__order__"] = order  # type: ignore[assignment]
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=None, help="工作根目录，默认 SHIORI_ROOT 或仓库根")
    parser.add_argument("--work-id", default=None, help="作品 id（也可用环境变量 WORK_ID）")
    parser.add_argument("--start-id", default="p00801", help="续跑起始段落 id")
    parser.add_argument("--chunk-size", type=int, default=600)
    parser.add_argument("--out-subdir", default="chunks_600")
    parser.add_argument("--prefix", default="chunk_600", help="输出文件名前缀")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    work_id = cfg.resolve_work_id(args.work_id)
    src_dir = cfg.chunks_dir(work_id)
    out_dir = cfg.root() / args.out_subdir / work_id

    rows = load_source_rows(src_dir)
    order: list[str] = rows.pop("__order__")  # type: ignore[assignment]

    m = ID_RE.match(args.start_id)
    if not m:
        raise SystemExit(f"bad start id: {args.start_id}")
    start_num = int(m.group(1))

    seq: list[str] = []
    num = start_num
    while f"p{num:05d}" in rows:
        seq.append(f"p{num:05d}")
        num += 1

    total = len(seq)
    if total == 0:
        raise SystemExit("no paragraphs selected")

    chunks: list[list[str]] = []
    for i in range(0, total, args.chunk_size):
        chunks.append(seq[i : i + args.chunk_size])

    report = {
        "start_id": args.start_id,
        "end_id": seq[-1],
        "paragraph_count": total,
        "chunk_size": args.chunk_size,
        "chunk_count": len(chunks),
        "out_dir": str(out_dir),
        "dry_run": args.dry_run,
        "last_chunk_size": len(chunks[-1]),
    }

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for idx, ids in enumerate(chunks, start=1):
            name = f"{args.prefix}_{idx:04d}.jsonl"
            target = out_dir / name
            if target.exists():
                report.setdefault("skipped_existing", []).append(name)
                continue
            lines = []
            for pid in ids:
                row = rows[pid]
                lines.append(
                    json.dumps(
                        {
                            "id": pid,
                            "chapter_id": row.get("chapter_id", ""),
                            "ja": row["ja"],
                        },
                        ensure_ascii=False,
                    )
                )
            target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            written.append({"file": name, "count": len(ids), "first": ids[0], "last": ids[-1]})
        report["written"] = written

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
