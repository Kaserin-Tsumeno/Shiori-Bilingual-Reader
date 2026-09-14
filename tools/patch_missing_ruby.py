from __future__ import annotations

r"""补注音：把 parts 中仍有未注音汉字的行单独送 API 重做。

只处理极少数行（通常十几行），请求极小，成功率极高。
重建时用「模型注音 + 原文字符」拼装，保证日文原文绝不被改写。

用法：
  py -3.11 tools\patch_missing_ruby.py --work-id my-novel
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_config as cfg  # noqa: E402
from api_pipeline import (  # noqa: E402
    RULES_TEMPLATE,
    Log,
    call_api,
    judge_batch,
    kanji_gap,
    parse_rows,
    rebuild_ja,
)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="补注音：修复仍有未注音汉字的行")
    ap.add_argument("--work-id", default=None, help="作品 id（也可用环境变量 WORK_ID）")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    work = cfg.resolve_work_id(args.work_id)
    model = args.model or cfg.default_model()
    system = RULES_TEMPLATE.format(glossary=cfg.glossary_text(work))

    parts_dir = cfg.parts_dir(work)
    units_dir = cfg.units_dir(work)
    log = Log(cfg.logs_dir() / f"patch_{work}_{time.strftime('%Y%m%d_%H%M%S')}.log")
    key = cfg.load_api_key()

    targets: dict[str, list[str]] = {}
    for path in sorted(parts_dir.glob("unit_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            _, _, gap = kanji_gap(r.get("ja_ruby_html", ""))
            if gap:
                targets.setdefault(path.name, []).append(r["id"])

    total = sum(len(v) for v in targets.values())
    log(f"需补注音的行：{total} 行，分布在 {len(targets)} 个单元")
    if not total:
        log("无需补注音。")
        return

    notice = (
        "这一行必须补全：原文中每一个汉字都要有 <ruby>漢字<rt>かな</rt></ruby>，一个都不能漏。"
        "同时 ja_ruby_html 必须与原文逐字符一致，不得增删改任何字符。"
    )

    fixed = 0
    for fname, ids in targets.items():
        src_rows = {
            json.loads(line)["id"]: json.loads(line)["ja"]
            for line in (units_dir / fname).read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        path = parts_dir / fname
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        by_id = {r["id"]: r for r in rows}

        for pid in ids:
            item = {"id": pid, "ja": src_rows[pid]}
            best = None
            latest = None
            for attempt in range(1, 5):
                try:
                    text = call_api(key, model, [item], system, notice)
                except Exception as e:
                    log(f"{pid} 第{attempt}次异常 {type(e).__name__}: {e}")
                    time.sleep(3)
                    continue
                parsed = parse_rows(text)
                if not parsed:
                    continue
                latest = parsed[0]
                judged = judge_batch([item], parsed)
                if judged["rows"]:
                    best = judged["rows"][0]
                    log(f"{pid} 补注音成功")
                    break

            if best is None:
                old = by_id[pid]
                # 用模型最新输出里的注音对重建（字符全部取自原文，不会改写日文）
                source_html = (latest or {}).get("ja_ruby_html") or old.get("ja_ruby_html", "")
                rebuilt, gap = rebuild_ja(src_rows[pid], source_html)
                best = {
                    "id": pid,
                    "zh": old.get("zh", "") or (latest or {}).get("zh", ""),
                    "ja_ruby_html": rebuilt,
                    "ruby_notes": (old.get("ruby_notes", "") + " 经脚本重建补注音").strip(),
                }
                log(f"{pid} 直接重试未通过，改用重建（剩余缺口 {gap or '无'}）")
            else:
                fixed += 1
            by_id[pid] = best

        ordered = [by_id[r["id"]] for r in rows]
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in ordered) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        log(f"{fname} 已更新")

    log(f"完成：成功补注音 {fixed}/{total} 行")


if __name__ == "__main__":
    main()
