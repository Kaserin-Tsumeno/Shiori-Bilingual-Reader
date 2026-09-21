from __future__ import annotations
import sys as _sys
from pathlib import Path as _ShioriPath

_sys.path.insert(0, str(_ShioriPath(__file__).resolve().parent))
import shiori_config as cfg

"""把 parts/ 下的单元产物累积合并为 chunk_600_XXXX.zh_ruby.jsonl。

数据来源（后者覆盖前者）：
  1. 已存在的 translations/<work>/chunk_600_*.zh_ruby.jsonl（累积池，支持续跑）
  2. translations/<work>/parts/*.jsonl（新产出的单元片段）

分块边界取自 chunks_600/<work>/chunk_600_*.jsonl，因此产出文件的
段落范围与续跑分块完全对齐。脚本幂等：重复运行结果一致。

用法：
  py -3.11 tools\merge_parts_to_chunks.py --work-id my-novel
  # 追加 --prune-parts 在成功吸收后删除 parts 中的单元文件
"""

import argparse
import html
import json
import re
from pathlib import Path

UNIT_RE = re.compile(r"^unit_(\d{4})")

TAG_RE = re.compile(r"<[^>]+>")
RT_RE = re.compile(r"<rt[^>]*>.*?</rt>", re.I | re.S)
BR_RE = re.compile(r"<br\s*/?>", re.I)


def strip_html(value: str) -> str:
    """把译文净化为纯文本：<br>→换行、去 <rt> 读音、去其余标签、HTML 反转义。

    少数原文 ja 内嵌上游预置的 <ruby><rb>…</rb><rt>…</rt></ruby>，译文里不应
    出现这些标签，因此统一在合并阶段净化，保证阅读器中文栏是纯文本。
    """
    value = BR_RE.sub("\n", value)
    value = RT_RE.sub("", value)
    value = TAG_RE.sub("", value)
    return html.unescape(value)


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path.name}:{lineno} JSON 解析失败: {exc}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=None, help="工作根目录，默认 SHIORI_ROOT 或仓库根")
    parser.add_argument("--work-id", default=None, help="作品 id（也可用环境变量 WORK_ID）")
    parser.add_argument("--prune-parts", action="store_true")
    args = parser.parse_args()

    work_id = cfg.resolve_work_id(args.work_id)
    trans_dir = cfg.trans_dir(work_id)
    parts_dir = trans_dir / "parts"
    chunks_dir = cfg.chunks_600_dir(work_id)
    trans_dir.mkdir(parents=True, exist_ok=True)

    # 分块边界
    boundaries: list[tuple[str, list[str]]] = []
    for path in sorted(chunks_dir.glob("*.jsonl")):
        ids = [str(r["id"]) for r in load_jsonl(path)]
        boundaries.append((path.stem.replace("chunk_600_", ""), ids))
    if not boundaries:
        raise SystemExit(f"未找到续跑分块: {chunks_dir}")

    pool: dict[str, dict] = {}
    absorbed_old = 0
    for path in sorted(trans_dir.glob("chunk_600_*.zh_ruby.jsonl")):
        for row in load_jsonl(path):
            pid = str(row.get("id", ""))
            if pid:
                pool[pid] = row
                absorbed_old += 1

    part_files = sorted(parts_dir.glob("*.jsonl")) if parts_dir.exists() else []

    # 同一单元常同时留下「合并主文件」与「早期片段」。主文件是最新成品，必须优先采用；
    # 否则按文件名排序会把 unit_XXXX.partD 这类旧片段排在主文件之后，用修正前的内容覆盖成品。
    unit_groups: dict[int, list[Path]] = {}
    others: list[Path] = []
    for path in part_files:
        m = UNIT_RE.match(path.name)
        if m:
            unit_groups.setdefault(int(m.group(1)), []).append(path)
        else:
            others.append(path)

    ordered: list[Path] = list(others)
    for num in sorted(unit_groups):
        main = parts_dir / f"unit_{num:04d}.jsonl"
        if main.exists():
            ordered.append(main)
        else:
            ordered.extend(unit_groups[num])

    absorbed_parts = 0
    for path in ordered:
        for row in load_jsonl(path):
            pid = str(row.get("id", ""))
            if pid:
                pool[pid] = row
                absorbed_parts += 1

    known_ids = {pid for _, ids in boundaries for pid in ids}
    extra_ids = sorted(set(pool) - known_ids)

    report = {
        "pool_size": len(pool),
        "from_existing_chunks": absorbed_old,
        "from_parts": absorbed_parts,
        "part_file_count": len(part_files),
        "extra_ids_outside_chunks": extra_ids[:20],
        "extra_id_count": len(extra_ids),
        "chunks": [],
    }

    written_ids: set[str] = set()
    sanitized_zh: list[str] = []
    for label, ids in boundaries:
        present = [pid for pid in ids if pid in pool]
        if not present:
            continue
        target = trans_dir / f"chunk_600_{label}.zh_ruby.jsonl"
        lines = []
        for pid in ids:
            row = pool.get(pid)
            if row is None:
                continue
            zh = row.get("zh", "")
            if "<" in zh:
                zh = strip_html(zh)
                sanitized_zh.append(pid)
            lines.append(
                json.dumps(
                    {
                        "id": pid,
                        "zh": zh,
                        "ja_ruby_html": row.get("ja_ruby_html", ""),
                        "ruby_notes": row.get("ruby_notes", ""),
                    },
                    ensure_ascii=False,
                )
            )
        target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        written_ids.update(present)
        report["chunks"].append(
            {
                "file": target.name,
                "written": len(present),
                "chunk_total": len(ids),
                "complete": len(present) == len(ids),
                "first": present[0],
                "last": present[-1],
            }
        )

    if args.prune_parts:
        pruned = []
        for path in part_files:
            path.unlink()
            pruned.append(path.name)
        report["pruned_parts"] = pruned

    report["total_written_ids"] = len(written_ids)
    report["zh_sanitized_count"] = len(sanitized_zh)
    report["zh_sanitized_ids"] = sanitized_zh[:30]
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
