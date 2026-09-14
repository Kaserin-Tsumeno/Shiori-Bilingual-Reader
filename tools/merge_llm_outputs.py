from __future__ import annotations
import sys as _sys
from pathlib import Path as _KitPath

_sys.path.insert(0, str(_KitPath(__file__).resolve().parent))
import kit_config as cfg

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
    index = json.loads((root / "works" / "index.json").read_text(encoding="utf-8"))
    css = (root / "assets" / "reader.css").read_text(encoding="utf-8")
    js = (root / "assets" / "reader.js").read_text(encoding="utf-8")
    # 数据以 <script type="application/json"> 承载：浏览器只做文本扫描，
    # 之后由原生 JSON.parse 解析，比 JS 引擎解析同体积对象字面量快数倍。
    # 转义 "</" 避免序列意外闭合 script 标签。
    index_json = json.dumps(index, ensure_ascii=False).replace("</", "<\\/")
    works_json = json.dumps({work["work_id"]: work}, ensure_ascii=False).replace("</", "<\\/")
    html_doc = f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>双语对照阅读器</title>
<style>{css}</style>
</head>
<body>
<div id="overlay" class="overlay"></div>
<div class="app">
  <aside id="sidebar" class="sidebar">
    <div class="sidebar-head">
      <div><div class="brand">双语对照阅读器</div><div class="subtle">中日对照 · 假名注音</div></div>
      <select id="workSelect" title="作品"></select>
      <div class="chapter-tools"><input id="sideSearch" type="search" placeholder="筛选章节"></div>
    </div>
    <div class="side-tabs">
      <button type="button" data-tab="chapters" class="active">目录</button>
      <button type="button" data-tab="bookmarks">书签<span class="tab-count" id="bmCount">0</span></button>
    </div>
    <nav id="chapterList" class="chapter-list" aria-label="章节目录"></nav>
    <nav id="bookmarkList" class="chapter-list" aria-label="书签列表" hidden></nav>
  </aside>
  <section class="content">
    <header class="toolbar">
      <button id="menuToggle" class="menu-toggle">目录</button>
      <select id="chapterSelect" title="章节"></select>
      <div class="segmented" aria-label="对照模式">
        <button data-layout="side" class="active">左右</button>
        <button data-layout="stack">上下</button>
        <button data-layout="ja">日文</button>
        <button data-layout="zh">中文</button>
      </div>
      <label class="toggle"><input id="rubyToggle" type="checkbox" checked> 注音</label>
      <button id="fontMinus" title="减小字号">A-</button>
      <button id="fontPlus" title="放大字号">A+</button>
      <button id="darkToggle" title="切换深浅色">暗</button>
      <input id="searchBox" type="search" placeholder="搜索日文或中文">
      <button id="prevChapter">上一章</button>
      <button id="nextChapter">下一章</button>
    </header>
    <aside id="status"></aside>
    <main id="reader" class="reader layout-side"><div class="loading-hint">正在加载…</div></main>
  </section>
</div>
<script id="reader-index" type="application/json">{index_json}</script>
<script id="reader-works" type="application/json">{works_json}</script>
<script>{js}</script>
</body>
</html>
'''
    (root / "reader.html").write_text(html_doc, encoding="utf-8", newline="\n")


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


