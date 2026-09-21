from __future__ import annotations

r"""自动裁决读音冲突：同一个词在全书出现了多种读音时，让模型定夺并落表。

为什么需要它：
  3 万段的作品里，人名读音最容易漂移（田中 たなか(2716) vs でんちゅう(1050)）。
  harvest_readings 能把冲突找出来，但原来的流程要求【人停下来填表】——
  20 本书就无法无人值守。这里改成：模型带着语境裁决，直接写进 readings.json，
  并把裁决理由写进报告，人想抽查时再看。

用法：
  py -3.11 tools\auto_readings.py --work-id my-novel
  py -3.11 tools\auto_readings.py --work-id my-novel --dry-run     # 只裁决不落表
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harvest_readings  # noqa: E402
import llm  # noqa: E402
import schema  # noqa: E402
import shiori_config as cfg  # noqa: E402

SYSTEM = """你是日本小说的校对编辑，负责确定专有名词的读音。

你会拿到一个词、它在全书出现过的若干读音（含次数）、以及它在正文里的语境。
请判断哪个读音是正确的，或说明必须按语境区分。

只输出 JSON，不要解释、不要 markdown 代码块。"""

USER_TEMPLATE = """词：{term}

全书出现过的读音（括号内是次数）：
{variants}

它在正文里的语境：
{contexts}

请判断。

规则：
- 若是人名/地名/组织名，按该作品的实际语境判断，通常取出现次数最多的那个
- 若确实需要按语境区分（例如「角」在「角を曲がる」读 かど、在「三角形」读 かく），
  请在 rule 里写清区分方式，并把 reading 设为出现最多的读音
- 没有把握就标 confidence "低"，不要硬猜

输出格式（严格）：
{{"reading":"ひさし","confidence":"高","kind":"人名","reason":"全书中作为角色名出现，くじ 是误注","rule":""}}"""


def find_contexts(paras: list[dict], term: str, limit: int = 3, width: int = 55) -> list[str]:
    out = []
    for p in paras:
        src = schema.get_field(p, "src")
        i = src.find(term)
        if i >= 0:
            snippet = src[max(0, i - width) : i + len(term) + width].replace("\n", " ")
            out.append(snippet)
            if len(out) >= limit:
                break
    return out


def resolve(work_id: str, log=print, min_count: int = 3, batch_size: int = 12) -> list[dict]:
    candidates, conflicts, _noise, glossary_terms, existing = harvest_readings.collect_candidates(
        work_id, min_count
    )
    if not conflicts:
        log("  没有读音冲突需要裁决")
        return []
    work = json.loads(cfg.work_file(work_id).read_text(encoding="utf-8"))
    paras = work["paragraphs"]

    items = list(conflicts.items())
    verdicts: list[dict] = []
    for start in range(0, len(items), batch_size):
        group = items[start : start + batch_size]
        blocks = []
        for term, variants in group:
            ctx = find_contexts(paras, term)
            kind_hint = "（术语表中的专名）" if term in glossary_terms else ""
            blocks.append(
                f"### {term}{kind_hint}\n"
                f"读音：{'、'.join(variants)}\n"
                f"语境：\n" + "\n".join(f"  - {c}" for c in ctx)
            )
        user = (
            "下面有多个词需要判断读音。请对**每一个**词给出结论，输出 JSON 数组：\n"
            '{"results":[{"term":"田中","reading":"たなか","confidence":"高",'
            '"kind":"人名","reason":"...","rule":""}]}\n\n' + "\n\n".join(blocks)
        )
        log(f"  裁决第 {start // batch_size + 1} 批（{len(group)} 个词）…")
        answered: set[str] = set()
        try:
            data = llm.chat_json(SYSTEM, user, log=log)
            results = data.get("results", data) if isinstance(data, dict) else data
            for r in results if isinstance(results, list) else []:
                term = str(r.get("term", "")).strip()
                reading = str(r.get("reading", "")).strip()
                if term and reading:
                    answered.add(term)
                    verdicts.append({
                        "term": term,
                        "reading": reading,
                        "confidence": str(r.get("confidence", "")).strip() or "低",
                        "kind": str(r.get("kind", "")).strip(),
                        "reason": str(r.get("reason", "")).strip(),
                        "rule": str(r.get("rule", "")).strip(),
                        "variants": conflicts.get(term, []),
                    })
        except Exception as e:                                        # noqa: BLE001
            log(f"  本批裁决失败：{e}")

        # 关键：不留待定。模型没答或答漏的词，回退为「出现次数最多的读音」并标低置信度，
        # 保证每个冲突词都有确定结论，流水线不必停下等人。
        for term, variants in group:
            if term in answered or not variants:
                continue
            top = str(variants[0]).split("(")[0].strip()
            if not top:
                continue
            verdicts.append({
                "term": term,
                "reading": top,
                "confidence": "低",
                "kind": "",
                "reason": "模型未给出结论，回退为出现次数最多的读音",
                "rule": "",
                "variants": variants,
            })
    return verdicts


def resolve_and_write(work_id: str, log=print, min_count: int = 3,
                      batch_size: int = 12, dry_run: bool = False) -> dict:
    verdicts = resolve(work_id, log=log, min_count=min_count, batch_size=batch_size)
    existing = schema.load_readings(cfg.library_dir(), work_id)
    merged: dict[str, dict] = {}
    for term, info in existing.items():
        merged[term] = {"reading": info["reading"]}
        if info.get("confidence"):
            merged[term]["confidence"] = info["confidence"]

    added = 0
    for v in verdicts:
        if v["term"] in merged:
            continue
        entry = {"reading": v["reading"]}
        if v["confidence"] != "高":
            entry["confidence"] = v["confidence"]
        if v.get("rule"):
            entry["rule"] = v["rule"]
        merged[v["term"]] = entry
        added += 1

    report = {
        "冲突数": len(verdicts),
        "新增读音条目": added,
        "累计条数": len(merged),
        "低置信度": [f"{v['term']}→{v['reading']}" for v in verdicts if v["confidence"] != "高"],
        "回退项（模型未定，取最高频）": [v["term"] for v in verdicts if not v["kind"] and v["confidence"] == "低"],
        "按语境区分": [f"{v['term']}: {v['rule']}" for v in verdicts if v.get("rule")],
    }

    if not dry_run and merged:
        payload = {
            "_说明": "读音表：专名读音一次定稿、全书强制。confidence 为「低」的建议抽查；"
                     "rule 表示该词需按语境区分读音。",
            **merged,
        }
        cfg.work_dir(work_id).mkdir(parents=True, exist_ok=True)
        (cfg.work_dir(work_id) / "readings.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        report["已写入"] = str(cfg.work_dir(work_id) / "readings.json")

    (cfg.work_dir(work_id) / "readings.verdicts.json").write_text(
        json.dumps(verdicts, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="栞 · 自动裁决读音冲突")
    ap.add_argument("--work-id", default=None)
    ap.add_argument("--min-count", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=12)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    work_id = cfg.resolve_work_id(args.work_id)
    report = resolve_and_write(work_id, log=print, min_count=args.min_count,
                               batch_size=args.batch_size, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
