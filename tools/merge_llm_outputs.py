from __future__ import annotations
import sys as _sys
from pathlib import Path as _KitPath

_sys.path.insert(0, str(_KitPath(__file__).resolve().parent))
import kit_config as cfg
import reader_builder

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
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_outputs(trans_dir: Path) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for path in sorted(trans_dir.glob("*.jsonl")):
        for row in load_jsonl(path):
            pid = str(row["id"])
            current = merged.setdefault(pid, {"id": pid})
            if row.get("zh"):
                current["zh"] = row["zh"]
            if row.get("ja_ruby_html"):
                current["ja_ruby_html"] = row["ja_ruby_html"]
                current["ruby_notes"] = row.get("ruby_notes", "")
    return merged


def rebuild_embedded_reader(root: Path, work: dict) -> None:
    """重建 reader.html（模板统一由 reader_builder 提供）。"""
    reader_builder.write_reader(root, work)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=None, help="工作根目录，默认 KIT_ROOT 或仓库根")
    parser.add_argument("--work-id", default=None, help="作品 id（也可用环境变量 WORK_ID）")
    args = parser.parse_args()

    work_id = cfg.resolve_work_id(args.work_id)
    root = args.root or cfg.kit_root()
    work_path = root / "works" / f"{work_id}.json"
    index_path = root / "works" / "index.json"
    trans_dir = cfg.trans_dir(work_id)
    work = json.loads(work_path.read_text(encoding="utf-8"))
    outputs = load_outputs(trans_dir)

    mismatches = []
    updated_zh = 0
    updated_ruby = 0
    for para in work["paragraphs"]:
        row = outputs.get(para["id"])
        if not row:
            continue
        if row.get("zh"):
            para["zh"] = row["zh"]
            para["translation_status"] = "translated"
            updated_zh += 1
        if row.get("ja_ruby_html"):
            plain = strip_ruby_html(row["ja_ruby_html"])
            if plain == strip_ruby_html(para["ja"]):
                para["ja_ruby_html"] = row["ja_ruby_html"].replace("\n", "<br>")
                para["ruby_status"] = "generated"
                para["ruby_notes"] = row.get("ruby_notes", "")
                updated_ruby += 1
            else:
                mismatches.append({"id": para["id"], "expected": para["ja"], "actual": plain})

    translated_count = sum(1 for p in work["paragraphs"] if p.get("translation_status") == "translated")
    ruby_generated_count = sum(1 for p in work["paragraphs"] if p.get("ruby_status") == "generated")
    work["stats"]["translated_count"] = translated_count
    work["stats"]["pending_count"] = len(work["paragraphs"]) - translated_count
    work["stats"]["ruby_generated_count"] = ruby_generated_count
    work["stats"]["ruby_pending_count"] = len(work["paragraphs"]) - ruby_generated_count
    work["stats"]["ruby_status"] = "generated" if ruby_generated_count == len(work["paragraphs"]) else "pending_llm"

    # Update translated chapter titles if the first paragraph of a chapter is translated.
    by_id = {p["id"]: p for p in work["paragraphs"]}
    for chapter in work["chapters"]:
        first = by_id.get(chapter["start_paragraph_id"])
        if first and first.get("zh"):
            chapter["title_zh"] = first["zh"].splitlines()[0]

    work_path.write_text(json.dumps(work, ensure_ascii=False, indent=2), encoding="utf-8")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for item in index.get("works", []):
        if item.get("work_id") == work_id:
            item["translated_count"] = translated_count
            item["paragraph_count"] = len(work["paragraphs"])
            item["ruby_status"] = work["stats"]["ruby_status"]
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    rebuild_embedded_reader(root, work)

    report = {
        "updated_zh": updated_zh,
        "updated_ruby": updated_ruby,
        "translated_count": translated_count,
        "pending_count": work["stats"]["pending_count"],
        "ruby_generated_count": ruby_generated_count,
        "ruby_pending_count": work["stats"]["ruby_pending_count"],
        "ruby_mismatch_count": len(mismatches),
        "ruby_mismatch_ids": [m["id"] for m in mismatches[:20]],
        "reader_html": str(root / "reader.html"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if mismatches:
        bad_path = trans_dir / "ruby_mismatches.json"
        bad_path.write_text(json.dumps(mismatches, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()


