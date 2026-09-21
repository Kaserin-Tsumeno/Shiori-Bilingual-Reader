from __future__ import annotations
import sys as _sys
from pathlib import Path as _ShioriPath

_sys.path.insert(0, str(_ShioriPath(__file__).resolve().parent))
import schema
import shiori_config as cfg

r"""最终验收报告：对作品 JSON 做交付级体检。

检查项：
  1. 段落总数与原稿是否一致（不漏段）
  2. 中文译文完整率（zh 非空）
  3. ruby 还原一致性（去掉 ruby 后必须与日文原文逐字相同）
  4. 汉字注音覆盖率（要求 100%）
  5. 兜底重建行、ruby_notes 标记行清单
  6. 译文异常（含 HTML 标签、空译文、疑似未翻译的假名残留）

用法：py -3.11 tools\final_report.py
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

# 工作根目录与作品 id 由 shiori_config 解析（SHIORI_ROOT / WORK_ID / --work-id）


TAG_RE = re.compile(r"<[^>]+>")
RT_RE = re.compile(r"<rt[^>]*>.*?</rt>", re.I | re.S)
BR_RE = re.compile(r"<br\s*/?>", re.I)
KANJI = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")  # 不含「々」：重复符号，无独立读音
KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")


def strip_ruby(v: str) -> str:
    return html.unescape(TAG_RE.sub("", RT_RE.sub("", BR_RE.sub("\n", v))))


def kanji_gap(html_str: str) -> tuple[int, int, str]:
    plain = TAG_RE.sub("", RT_RE.sub("", BR_RE.sub("\n", html_str)))
    covered: list[str] = []
    for m in re.finditer(r"<ruby>(.*?)<rt[^>]*>.*?</rt></ruby>", html_str, re.S):
        covered.extend(KANJI.findall(TAG_RE.sub("", m.group(1))))
    uncovered = list(KANJI.findall(plain))
    for ch in covered:
        if ch in uncovered:
            uncovered.remove(ch)
    return len(KANJI.findall(plain)), len(covered), "".join(uncovered)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description='作品交付体检')
    ap.add_argument('--work-id', default=None, help='作品 id（也可用环境变量 WORK_ID）')
    args = ap.parse_args()
    work_id = cfg.resolve_work_id(args.work_id)
    work = json.loads(cfg.work_file(work_id).read_text(encoding='utf-8'))
    paras = work["paragraphs"]

    stats = {
        "段落总数": len(paras),
        "章节数": len(work.get("chapters", [])),
        "已有中文译文": 0,
        "ruby 已生成": 0,
        "ruby 还原不一致": 0,
        "译文为空": 0,
        "译文含 HTML 标签": 0,
        "译文含假名残留": 0,
        "注音未覆盖的行": 0,
        "兜底重建标记行": 0,
        "ruby_notes 非空行": 0,
    }
    total_kanji = covered_kanji = 0
    bad_ruby: list[str] = []
    gap_rows: list[tuple[str, str]] = []
    fallback_rows: list[str] = []
    kana_rows: list[str] = []
    tag_rows: list[str] = []

    for p in paras:
        pid = p["id"]
        ja = schema.get_field(p, "src")
        zh = schema.get_field(p, "tgt").strip()
        rh = schema.get_field(p, "src_annotated")
        notes = schema.get_field(p, "notes")

        if zh:
            stats["已有中文译文"] += 1
            if "<" in zh:
                stats["译文含 HTML 标签"] += 1
                tag_rows.append(pid)
            if KANA.search(zh):
                stats["译文含假名残留"] += 1
                kana_rows.append(pid)
        else:
            stats["译文为空"] += 1

        if p.get("ruby_status") == "generated" and rh:
            stats["ruby 已生成"] += 1
            if strip_ruby(rh) != strip_ruby(ja):
                stats["ruby 还原不一致"] += 1
                bad_ruby.append(pid)
            t, c, gap = kanji_gap(rh)
            total_kanji += t
            covered_kanji += c
            if t and c < t:
                stats["注音未覆盖的行"] += 1
                gap_rows.append((pid, gap))
        if "兜底重建" in notes:
            stats["兜底重建标记行"] += 1
            fallback_rows.append(pid)
        if notes.strip():
            stats["ruby_notes 非空行"] += 1

    stats["汉字总数"] = total_kanji
    stats["已注音汉字数"] = covered_kanji
    stats["注音覆盖率"] = f"{covered_kanji / total_kanji * 100:.2f}%" if total_kanji else "n/a"

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print()
    print("ruby 还原不一致（必须为 0）:", bad_ruby[:20], f"共 {len(bad_ruby)}")
    print("注音未覆盖（应为 0）:", gap_rows[:20], f"共 {len(gap_rows)}")
    print("兜底重建行:", fallback_rows[:20], f"共 {len(fallback_rows)}")
    print("译文含假名残留:", kana_rows[:20], f"共 {len(kana_rows)}")
    print("译文含 HTML 标签:", tag_rows[:20], f"共 {len(tag_rows)}")


if __name__ == "__main__":
    main()
