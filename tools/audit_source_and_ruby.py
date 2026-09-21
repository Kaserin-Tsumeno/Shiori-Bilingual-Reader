from __future__ import annotations
import sys as _sys
from pathlib import Path as _ShioriPath

_sys.path.insert(0, str(_ShioriPath(__file__).resolve().parent))
import schema
import shiori_config as cfg

r"""核查原稿与作品 JSON 的段落一致性，并统计 ruby 汉字覆盖率。

用法：
  py -3.11 tools\audit_source_and_ruby.py
"""

import argparse
import json
import re
import sys
from pathlib import Path

# 原稿与作品路径由 --work-id / --source 或环境变量决定


KANJI = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff々]")
TAG_RE = re.compile(r"<[^>]+>")
RT_RE = re.compile(r"<rt[^>]*>.*?</rt>", re.I | re.S)
BR_RE = re.compile(r"<br\s*/?>", re.I)


def source_blocks(text: str) -> list[str]:
    """按空行分段：连续非空行合并为一段，段内保留换行。"""
    blocks: list[str] = []
    cur: list[str] = []
    for line in text.split("\n"):
        if line.strip():
            cur.append(line.rstrip())
        else:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def ruby_coverage(html: str) -> tuple[int, int]:
    """返回 (汉字总数, 被 ruby 覆盖的汉字数)。"""
    plain = RT_RE.sub("", BR_RE.sub("\n", html))
    total_kanji = len(KANJI.findall(TAG_RE.sub("", plain)))
    covered = 0
    for m in re.finditer(r"<ruby>(.*?)<rt[^>]*>.*?</rt></ruby>", html, re.S):
        covered += len(KANJI.findall(TAG_RE.sub("", m.group(1))))
    return total_kanji, covered


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="核查原稿与作品数据一致性、统计 ruby 覆盖率")
    ap.add_argument("--work-id", default=None, help="作品 id（也可用环境变量 WORK_ID）")
    ap.add_argument("--source", type=Path, default=None, help="原始日文稿件路径，默认 sources/<work_id>.txt")
    args = ap.parse_args()
    work_id = cfg.resolve_work_id(args.work_id)
    src_path = args.source or cfg.source_file(work_id)
    if not src_path.exists():
        raise SystemExit(f"找不到原稿：{src_path}（用 --source 指定）")
    src_text = src_path.read_text(encoding="utf-8")
    blocks = source_blocks(src_text)
    work = json.loads(cfg.work_file(work_id).read_text(encoding="utf-8"))
    paras = work["paragraphs"]

    print("=== 段落结构核查 ===")
    print(f"原稿分段块数: {len(blocks)}")
    print(f"作品 JSON 段数: {len(paras)}")

    src_lines = [l for l in src_text.split("\n") if l.strip()]
    work_lines = []
    for p in paras:
        work_lines.extend(schema.get_field(p, "src").split("\n"))
    print(f"原稿非空行数: {len(src_lines)} / 作品 ja 非空行数: {len(work_lines)}")

    mismatch = 0
    first_bad = None
    for i, (a, b) in enumerate(zip(src_lines, work_lines)):
        if a.rstrip() != b.rstrip():
            mismatch += 1
            if first_bad is None:
                first_bad = (i + 1, a[:60], b[:60])
    print(f"逐行不一致数: {mismatch}")
    if first_bad:
        print(f"首个不一致(行{first_bad[0]}): 原稿={first_bad[1]!r} 作品={first_bad[2]!r}")

    print()
    print("=== ruby 汉字覆盖率（已产出部分）===")
    total = covered = 0
    no_ruby_paras = []
    for p in paras:
        html = schema.get_field(p, "src_annotated")
        if p.get("ruby_status") != "generated":
            continue
        t, c = ruby_coverage(html)
        total += t
        covered += c
        if t > 0 and c < t:
            no_ruby_paras.append((p["id"], t - c, t))
    print(f"汉字总数: {total}")
    print(f"已注音汉字: {covered}")
    print(f"覆盖率: {covered / total * 100:.2f}%" if total else "n/a")
    print(f"存在未注音汉字的段落数: {len(no_ruby_paras)}")
    print("示例:", no_ruby_paras[:12])


if __name__ == "__main__":
    main()
