from __future__ import annotations

"""单元级质量校验：在调用 merge_llm_outputs.py 之前先拦截不合格输出。

校验项：
  1. 输出文件每行都是合法 JSON，且有 id / zh / ja_ruby_html 字段。
  2. id 序列与输入单元完全一致（顺序相同、不漏不重）。
  3. zh 非空。
  4. ja_ruby_html 还原纯文本后与原 ja 完全一致（ruby 一致性）。
  5. 统计 ruby_notes 非空行数。

用法：
  py -3.11 tools\verify_unit.py --unit-file <原文单元> --out <输出文件> [--out <片段2> ...]
退出码 0 = 全部通过；1 = 存在不合格项（详见 JSON 报告）。
"""

import argparse
import html
import json
import re
from pathlib import Path

TAG_RE = re.compile(r"<[^>]+>")
RT_RE = re.compile(r"<rt[^>]*>.*?</rt>", re.I | re.S)
BR_RE = re.compile(r"<br\s*/?>", re.I)


def strip_ruby_html(value: str) -> str:
    value = BR_RE.sub("\n", value)
    value = RT_RE.sub("", value)
    value = TAG_RE.sub("", value)
    return html.unescape(value)


def load_jsonl(path: Path) -> list[dict]:
    rows = []
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
    parser.add_argument("--unit-file", type=Path, required=True, help="原文单元文件")
    parser.add_argument("--out", type=Path, action="append", required=True, help="输出文件，可多次指定")
    args = parser.parse_args()

    src = load_jsonl(args.unit_file)
    src_by_id = {str(r["id"]): r["ja"] for r in src}
    # 期望值同样经过 ruby 剥离：少数原文 ja 本身内嵌 <ruby><rb>…</rb><rt>…</rt></ruby>
    # （上游预置注音），判定口径必须与 merge_llm_outputs.py 保持一致。
    expected_by_id = {pid: strip_ruby_html(ja) for pid, ja in src_by_id.items()}
    src_order = [str(r["id"]) for r in src]

    produced: list[dict] = []
    for path in args.out:
        if not path.exists():
            raise SystemExit(f"输出文件不存在: {path}")
        produced.extend(load_jsonl(path))

    report: dict = {
        "unit_file": str(args.unit_file),
        "out_files": [str(p) for p in args.out],
        "source_count": len(src_order),
        "produced_count": len(produced),
    }

    ids = [str(r.get("id", "")) for r in produced]
    missing = [i for i in src_order if i not in set(ids)]
    extra = [i for i in ids if i not in src_by_id]
    dupes = sorted({i for i in ids if ids.count(i) > 1}) if len(set(ids)) != len(ids) else []
    order_ok = ids == src_order

    mismatch = []
    empty_zh = []
    missing_ruby = []
    notes_count = 0
    for row in produced:
        pid = str(row.get("id", ""))
        ja = expected_by_id.get(pid)
        if ja is None:
            continue
        zh = (row.get("zh") or "").strip()
        if not zh:
            empty_zh.append(pid)
        ruby_html = row.get("ja_ruby_html") or ""
        if not ruby_html:
            missing_ruby.append(pid)
        else:
            plain = strip_ruby_html(ruby_html)
            if plain != ja:
                mismatch.append(
                    {
                        "id": pid,
                        "expected": ja,
                        "actual": plain,
                    }
                )
        if (row.get("ruby_notes") or "").strip():
            notes_count += 1

    report.update(
        {
            "id_order_ok": order_ok,
            "missing_ids": missing[:20],
            "missing_count": len(missing),
            "extra_ids": extra[:20],
            "extra_count": len(extra),
            "duplicate_ids": dupes[:20],
            "empty_zh_count": len(empty_zh),
            "empty_zh_ids": empty_zh[:20],
            "missing_ruby_count": len(missing_ruby),
            "missing_ruby_ids": missing_ruby[:20],
            "ruby_mismatch_count": len(mismatch),
            "ruby_mismatch_ids": [m["id"] for m in mismatch[:20]],
            "ruby_notes_nonempty": notes_count,
        }
    )

    ok = (
        order_ok
        and not missing
        and not extra
        and not dupes
        and not empty_zh
        and not missing_ruby
        and not mismatch
    )
    report["ok"] = ok
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if mismatch:
        bad = args.out[0].parent / f"{args.out[0].stem}.mismatch.json"
        bad.write_text(json.dumps(mismatch, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[mismatch detail] {bad}")

    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
