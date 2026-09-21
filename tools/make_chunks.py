from __future__ import annotations

r"""分块：把作品数据切成生产用的工作单元与交付分块。

从 library/<work_id>/chunks/（init 生成的原料分块）中提取连续段落，
重新切成固定大小，写到：

  --target units         → library/<work_id>/units/       默认 100 段/块（生产的调度单位）
  --target chunks_600    → library/<work_id>/chunks_600/  默认 600 段/块（交付分块）

只搬运原文字段，不修改 ja 内容。已存在的分块不覆盖（可安全重复运行）。

用法：
  py -3.11 tools\make_chunks.py --work-id my-novel --target units
  py -3.11 tools\make_chunks.py --work-id my-novel --target chunks_600
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shiori_config as cfg  # noqa: E402

ID_RE = re.compile(r"^p(\d+)$")

# target → (默认块大小, 文件名前缀, 输出目录解析函数)
TARGETS = {
    "units": (100, "unit", cfg.units_dir),
    "chunks_600": (600, "chunk_600", cfg.chunks_600_dir),
    "chunks": (80, "chunk", cfg.chunks_dir),
}


def load_source_rows(src_dir: Path) -> dict:
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
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="栞 · 分块（工作单元 / 交付分块）")
    parser.add_argument("--work-id", default=None, help="作品 id（也可用环境变量 WORK_ID）")
    parser.add_argument("--target", choices=sorted(TARGETS), default="units",
                        help="units=生产工作单元；chunks_600=交付分块；chunks=原料分块")
    parser.add_argument("--start-id", default="p00001", help="起始段落 id")
    parser.add_argument("--chunk-size", type=int, default=None, help="默认 units=100 / chunks_600=600 / chunks=80")
    parser.add_argument("--prefix", default=None, help="输出文件名前缀，默认按 target 推导")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    work_id = cfg.resolve_work_id(args.work_id)
    default_size, default_prefix, dir_of = TARGETS[args.target]
    chunk_size = args.chunk_size or default_size
    prefix = args.prefix or default_prefix

    src_dir = cfg.chunks_dir(work_id)
    if not src_dir.exists() or not any(src_dir.glob("*.jsonl")):
        raise SystemExit(f"未找到原料分块：{src_dir}\n请先运行 tools\\init_work.py")
    out_dir = dir_of(work_id)

    rows = load_source_rows(src_dir)
    order: list[str] = rows.pop("__order__")  # type: ignore[assignment]

    m = ID_RE.match(args.start_id)
    if not m:
        raise SystemExit(f"起始 id 不合法: {args.start_id}")
    num = int(m.group(1))

    seq: list[str] = []
    while f"p{num:05d}" in rows:
        seq.append(f"p{num:05d}")
        num += 1
    if not seq:
        raise SystemExit("没有选中任何段落")

    chunks = [seq[i : i + chunk_size] for i in range(0, len(seq), chunk_size)]

    report = {
        "work_id": work_id,
        "target": args.target,
        "start_id": args.start_id,
        "end_id": seq[-1],
        "paragraph_count": len(seq),
        "chunk_size": chunk_size,
        "chunk_count": len(chunks),
        "out_dir": str(out_dir),
        "dry_run": args.dry_run,
        "last_chunk_size": len(chunks[-1]),
    }

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for idx, ids in enumerate(chunks, start=1):
            name = f"{prefix}_{idx:04d}.jsonl"
            target = out_dir / name
            if target.exists():
                report.setdefault("skipped_existing", []).append(name)
                continue
            lines = [
                json.dumps(
                    {"id": pid, "chapter_id": rows[pid].get("chapter_id", ""), "ja": rows[pid]["ja"]},
                    ensure_ascii=False,
                )
                for pid in ids
            ]
            target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            written.append({"file": name, "count": len(ids), "first": ids[0], "last": ids[-1]})
        report["written"] = written

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
