from __future__ import annotations

r"""自动抽取术语表：让模型从原稿里找出专有名词并给出目标语言译名。

设计原则（重要）：
  判断结果**直接落成 glossary.json**，流水线不中断等人确认。
  模型自评「不确定」的条目会写进报告，人想抽查时再看——
  而不是"不确认就不往下走"。这样 20 本书才能真正无人值守。

用法：
  py -3.11 tools\auto_terms.py --work-id my-novel
  py -3.11 tools\auto_terms.py --work-id my-novel --sample-chars 40000 --dry-run
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm  # noqa: E402
import schema  # noqa: E402
import shiori_config as cfg  # noqa: E402

SYSTEM = """你是日本小说的编辑助手，负责整理「专有名词对照表」。

你的任务是：从给定的小说片段中，找出**需要全书统一译法**的专有名词，
并给出目标语言的推荐译名。

只输出 JSON，不要解释、不要 markdown 代码块。"""

USER_TEMPLATE = """下面是一部{src_name}小说的若干片段（用 --- 分隔）。

请找出其中需要全书统一译法的专有名词：人名、地名、组织名、特殊称号、店名、专有术语。
对每个词给出推荐{tgt_name}译名。

要求：
- 只列真正需要统一的（重复出现、或容易译法不一的），普通词汇不要列
- 人名按当地姓名习惯译成{tgt_name}汉字
- 你有把握的标 "高"，不确定的标 "低"（宁可标低，不要硬猜）
- 最多 120 条，按重要性排序

输出格式（严格）：
{{"terms":[{{"ja":"山田太郎","zh":"山田太郎","kind":"人名","confidence":"高"}}]}}

片段：
{body}"""

LANG_NAMES = {"ja": "日文", "zh-Hans": "中文", "zh-Hant": "中文", "en": "英文", "ko": "韩文"}


def sample_text(work_id: str, budget: int) -> str:
    """均匀采样原稿：专名往往在开头密集出现，但也要覆盖中后段。"""
    path = cfg.source_file(work_id)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if len(text) <= budget:
        return text
    pieces = 6
    seg = budget // pieces
    step = max(1, (len(text) - seg) // (pieces - 1))
    chunks = [text[i * step : i * step + seg] for i in range(pieces)]
    return "\n---\n".join(chunks)


def extract_terms(work_id: str, budget: int, tgt_lang: str, log=print) -> list[dict]:
    src_lang = schema.read_lang_block(
        json.loads(cfg.work_file(work_id).read_text(encoding="utf-8"))
    )["src"] if cfg.work_file(work_id).exists() else "ja"
    body = sample_text(work_id, budget)
    user = USER_TEMPLATE.format(
        src_name=LANG_NAMES.get(src_lang, src_lang),
        tgt_name=LANG_NAMES.get(tgt_lang, tgt_lang),
        body=body[:budget],
    )
    log(f"  采样 {len(body)} 字符，请求模型抽取专名…")
    data = llm.chat_json(SYSTEM, user, log=log)
    terms = data.get("terms", data) if isinstance(data, dict) else data
    out = []
    for t in terms if isinstance(terms, list) else []:
        ja = str(t.get("ja", "")).strip()
        zh = str(t.get("zh", "")).strip()
        if ja and zh:
            out.append({
                "ja": ja, "zh": zh,
                "kind": str(t.get("kind", "")).strip(),
                "confidence": str(t.get("confidence", "")).strip() or "高",
            })
    return out


def harvest_and_write(work_id: str, log=print, sample_chars: int = 20000,
                      tgt_lang: str = "zh-Hans", dry_run: bool = False) -> dict:
    """抽取并写入 glossary.json；返回 {terms, low_confidence}。"""
    terms = extract_terms(work_id, sample_chars, tgt_lang, log=log)
    existing = cfg.glossary(work_id)
    merged = dict(existing)
    for t in terms:
        merged.setdefault(t["ja"], t["zh"])

    low = [t for t in terms if t.get("confidence") != "高"]
    report = {
        "抽取条数": len(terms),
        "新增条数": len(merged) - len(existing),
        "累计条数": len(merged),
        "不确定": [f"{t['ja']}→{t['zh']}" for t in low],
    }

    if not dry_run and merged:
        cfg.glossary_path(work_id).parent.mkdir(parents=True, exist_ok=True)
        payload = {"_说明": "自动抽取的术语表；改这里即改全书译名。标 low 的建议抽查。",
                   **{k: v for k, v in merged.items()}}
        cfg.glossary_path(work_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        report["已写入"] = str(cfg.glossary_path(work_id))

    # 分档记录，便于报告里列出
    detail_path = cfg.work_dir(work_id) / "terms.report.json"
    detail_path.write_text(json.dumps({"terms": terms, **report}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    report["明细"] = str(detail_path)
    return report


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="栞 · 自动抽取术语表")
    ap.add_argument("--work-id", default=None)
    ap.add_argument("--sample-chars", type=int, default=20000)
    ap.add_argument("--tgt-lang", default="zh-Hans")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    work_id = cfg.resolve_work_id(args.work_id)
    if not cfg.source_file(work_id).exists():
        raise SystemExit(f"找不到原稿：{cfg.source_file(work_id)}")
    report = harvest_and_write(work_id, log=print, sample_chars=args.sample_chars,
                               tgt_lang=args.tgt_lang, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
