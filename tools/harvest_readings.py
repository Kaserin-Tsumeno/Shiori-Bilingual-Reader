from __future__ import annotations

r"""从已完成的作品里沉淀读音候选，供人工一次性审校。

思路：与其逐段核对几万段的注音，不如让机器把全书出现过的
「汉字词 → 读音」统计出来，重点标出**同一个词出现了多种读音**的情况
（那几乎一定是错的，或需要按语境区分）。人工只需审这张表，再回填全书。

用法：
  py -3.11 tools\harvest_readings.py --work-id my-novel
  py -3.11 tools\harvest_readings.py --work-id my-novel --min-count 3

产出（都在 library/<work_id>/ 下）：
  readings.candidates.json   候选读音表（审校后另存为 readings.json 即生效）
  readings.conflicts.md      一词多读的冲突清单（优先看这个）
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402
import shiori_config as cfg  # noqa: E402


def iter_pairs(html: str):
    for base, rt in re.findall(r"<ruby>(.*?)<rt[^>]*>(.*?)</rt></ruby>", html, re.S):
        base = schema.TAG_RE.sub("", base).strip()
        rt = schema.TAG_RE.sub("", rt).strip()
        if base and rt:
            yield base, rt


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="沉淀读音候选（供审校）")
    ap.add_argument("--work-id", default=None)
    ap.add_argument("--min-count", type=int, default=2, help="只统计出现次数不少于该值的词，默认 2")
    args = ap.parse_args()

    work_id = cfg.resolve_work_id(args.work_id)
    work = json.loads(cfg.work_file(work_id).read_text(encoding="utf-8"))
    paras = work["paragraphs"]

    counter: Counter[tuple[str, str]] = Counter()
    for p in paras:
        html = schema.get_field(p, "src_annotated")
        if html:
            counter.update(iter_pairs(html))

    # 按词归并读音
    by_term: dict[str, Counter[str]] = defaultdict(Counter)
    for (base, rt), n in counter.items():
        by_term[base][rt] += n

    existing = schema.load_readings(cfg.library_dir(), work_id)
    glossary_terms = cfg.glossary(work_id)

    candidates: dict[str, dict] = {}
    total_terms = 0
    for term, readings in sorted(by_term.items(), key=lambda kv: -sum(kv[1].values())):
        count = sum(readings.values())
        if count < args.min_count:
            continue
        total_terms += 1
        # 优先项：已在读音表里 > 术语表里的专名 > 出现最多的读法
        if term in existing:
            chosen, confidence = existing[term]["reading"], existing[term].get("confidence", "")
        elif term in glossary_terms:
            chosen, confidence = readings.most_common(1)[0][0], "待确认（专名）"
        else:
            chosen, confidence = readings.most_common(1)[0][0], ""
        item = {"reading": chosen, "count": count}
        if confidence:
            item["confidence"] = confidence
        if len(readings) > 1:
            item["variants"] = [f"{r}({n})" for r, n in readings.most_common()]
        candidates[term] = item

    out_dir = cfg.work_dir(work_id)
    (out_dir / "readings.candidates.json").write_text(
        json.dumps({"_说明": "审校后删掉 _说明 并另存为 readings.json 即生效；variants 表示同一词出现过多种读音，需要人工确认",
                    **candidates},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 只把"真正的可疑项"列为冲突：单字在不同词里读音不同是日语固有现象（彼→かれ/かの），
    # 不构成问题；需要人工确认的是多字词，以及术语表内的单字专名。
    def is_suspicious(term: str) -> bool:
        return len(term) >= 2 or term in glossary_terms or term in existing

    conflicts = {t: v["variants"] for t, v in candidates.items()
                 if v.get("variants") and is_suspicious(t)}
    noise = {t: v["variants"] for t, v in candidates.items()
             if v.get("variants") and not is_suspicious(t)}
    lines = ["# 一词多读（优先审校）", "",
             f"共 {len(conflicts)} 个词出现过多种读音。这些要么是错的，要么需要按语境区分。", ""]
    for term, variants in sorted(conflicts.items(), key=lambda kv: -sum(int(x.split("(")[1].rstrip(")")) for x in kv[1])):
        mark = " ← 术语表专名" if term in glossary_terms else ""
        lines.append(f"- **{term}**{mark}：" + "、".join(variants))
    if not conflicts:
        lines.append("（无冲突：全书同一词的读音一致）")
    (out_dir / "readings.conflicts.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "work_id": work_id,
        "扫描段落": len(paras),
        "不同词条": total_terms,
        "一词多读（需审校）": len(conflicts),
        "单字多读（正常现象，不列）": len(noise),
        "候选表": str(out_dir / "readings.candidates.json"),
        "冲突清单": str(out_dir / "readings.conflicts.md"),
        "已有读音表条数": len(existing),
    }, ensure_ascii=False, indent=2))
    print()
    print("一词多读示例（前 15）：")
    for term, variants in list(conflicts.items())[:15]:
        print(f"  {term}: " + "、".join(variants))


if __name__ == "__main__":
    main()
