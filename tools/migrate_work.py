from __future__ import annotations

r"""把旧格式作品迁移到新结构（字段语义化 + 语言声明 + 书库布局）。

旧格式（中日专用字段）        →  新格式（语言无关字段）
  paragraphs[].ja                    src
  paragraphs[].ja_ruby_html          src_annotated
  paragraphs[].zh                    tgt
  paragraphs[].ruby_notes            notes
  chapters[].title_ja / title_zh     title_src / title_tgt
  （无语言声明）                       lang: {src, tgt, annotation, views}

迁移是幂等的：已经是新格式的作品再跑一次不会有变化。

用法：
  py -3.11 tools\migrate_work.py --source <旧 work.json> --work-id my-novel
  py -3.11 tools\migrate_work.py --source <旧 work.json> --work-id my-novel --src-lang ja --tgt-lang zh-Hans
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402
import shiori_config as cfg  # noqa: E402


def migrate(work: dict, src_lang: str, tgt_lang: str) -> tuple[dict, dict]:
    changed = {"paragraphs": 0, "chapters": 0, "lang_added": False}

    paras = []
    for p in work.get("paragraphs", []):
        new = schema.normalize_paragraph(p)
        if any(k in p for k in ("ja", "zh", "ja_ruby_html", "ruby_notes")):
            changed["paragraphs"] += 1
        paras.append(new)

    chapters = []
    for c in work.get("chapters", []):
        new = schema.normalize_chapter(c)
        if "title_ja" in c or "title_zh" in c:
            changed["chapters"] += 1
        chapters.append(new)

    out = dict(work)
    out["paragraphs"] = paras
    out["chapters"] = chapters
    if not isinstance(out.get("lang"), dict):
        out["lang"] = schema.default_lang_block(src_lang, tgt_lang)
        changed["lang_added"] = True
    out.pop("source_language", None)
    out.pop("target_language", None)
    return out, changed


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="栞 · 迁移旧作品到新结构")
    ap.add_argument("--source", type=Path, required=True, help="旧格式的 work json")
    ap.add_argument("--work-id", required=True)
    ap.add_argument("--src-lang", default="ja")
    ap.add_argument("--tgt-lang", default="zh-Hans")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    work_id = args.work_id
    old = json.loads(args.source.read_text(encoding="utf-8"))
    new, changed = migrate(old, args.src_lang, args.tgt_lang)

    cfg.ensure_work_dirs(work_id)
    target = cfg.work_file(work_id)
    if target.exists() and not args.no_backup:
        shutil.copy2(target, target.with_suffix(".json.bak"))
    target.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")

    # 同步书库索引
    idx_path = cfg.index_file()
    try:
        index = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception:
        index = {"works": []}
    entry = {
        "work_id": work_id,
        "title": new.get("title", work_id),
        "path": f"library/{work_id}/work.json",
        "chapter_count": len(new.get("chapters", [])),
        "paragraph_count": len(new.get("paragraphs", [])),
        "translated_count": sum(1 for p in new.get("paragraphs", []) if schema.get_field(p, "tgt")),
        "updated_at": new.get("updated_at", ""),
    }
    index["works"] = [w for w in index.get("works", []) if w.get("work_id") != work_id]
    index["works"].append(entry)
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    idx_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "work_id": work_id,
        "迁移段落": changed["paragraphs"],
        "迁移章节": changed["chapters"],
        "补充语言声明": changed["lang_added"],
        "lang": new["lang"],
        "输出": str(target),
        "段落总数": len(new.get("paragraphs", [])),
        "下一步": f"py -3.11 tools\\make_chunks.py --work-id {work_id} --target units  （以及 --target chunks_600）",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
