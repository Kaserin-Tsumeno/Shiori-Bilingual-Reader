from __future__ import annotations

r"""批量导入：把 library/_inbox/ 里的原稿自动建档（推断 work_id / 标题 / 语言）并切块。

用法：
  # 1) 把原稿扔进收件箱
  library\_inbox\my-novel.txt          文件名即 work_id（英文/拼音/短横线）
  library\_inbox\中文书名.txt          中文名会自动分配 book-01、book-02…
  library\_inbox\another.txt           可在首行写「# 中文标题」指定作品标题

  # 2) 一条命令建档
  py -3.11 tools\import_books.py
  py -3.11 tools\import_books.py --auto-terms      # 顺带让模型抽取术语表
  py -3.11 tools\import_books.py --dry-run         # 只看会做什么

处理完成后原稿会被移到 library/_inbox/_done/，可安全重复运行。
"""

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402
import shiori_config as cfg  # noqa: E402

KANA_RE = re.compile(r"[\u3041-\u309f\u30a1-\u30fa]")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
TITLE_LINE_RE = re.compile(r"^#\s*(.+)$")


def detect_language(text: str) -> str:
    """按字符构成判断源语言。

    用**比例**而非绝对数量：短文本（几十字）也要能判对——
    假名是日语最可靠的信号，中文里几乎不出现假名。
    """
    sample = text[:20000]
    if not sample:
        return "ja"
    total = len(sample)
    kana = len(KANA_RE.findall(sample))
    hangul = len(HANGUL_RE.findall(sample))
    cjk = len(CJK_RE.findall(sample))
    jp_chars = kana + cjk

    if hangul and hangul / total > 0.10:
        return "ko"
    # 日语：假名在「汉字+假名」里占相当比例（书面语通常 30%–70%）
    if kana >= 3 and jp_chars and kana / jp_chars > 0.15:
        return "ja"
    if cjk / total > 0.15:
        return "zh-Hans"
    return "en"


def split_title(text: str) -> tuple[str | None, str]:
    """若首行是「# 标题」，取作作品标题并从正文中移除该行。"""
    lines = text.split("\n")
    for i, line in enumerate(lines[:5]):
        m = TITLE_LINE_RE.match(line.strip())
        if m:
            return m.group(1).strip(), "\n".join(lines[:i] + lines[i + 1 :])
    return None, text


def allocate_work_id(path: Path, used: set[str], index: int) -> str:
    stem = path.stem.strip()
    if SAFE_ID_RE.match(stem) and stem.lower() not in used:
        return stem
    candidate = f"book-{index:02d}"
    while candidate.lower() in used:
        index += 1
        candidate = f"book-{index:02d}"
    return candidate


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="栞 · 批量导入原稿")
    ap.add_argument("--inbox", type=Path, default=None, help="收件箱目录，默认 library/_inbox")
    ap.add_argument("--tgt-lang", default="zh-Hans")
    ap.add_argument("--chunk-tokens", type=int, default=20000, help="自动术语抽取的采样上限（字符）")
    ap.add_argument("--auto-terms", action="store_true", help="让模型从原稿抽取术语表")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    inbox = args.inbox or (cfg.library_dir() / "_inbox")
    inbox.mkdir(parents=True, exist_ok=True)
    done_dir = inbox / "_done"

    sources = sorted(p for p in inbox.glob("*.txt") if p.is_file())
    if not sources:
        print(json.dumps({
            "收件箱": str(inbox),
            "提示": "把原稿（UTF-8 txt）放进收件箱后重跑；文件名建议用英文/拼音短名作为 work_id",
            "找到": 0,
        }, ensure_ascii=False, indent=2))
        return

    existing = {w.get("work_id", "").lower() for w in json.loads(cfg.index_file().read_text(encoding="utf-8")).get("works", [])} \
        if cfg.index_file().exists() else set()

    results = []
    for i, src in enumerate(sources, start=1):
        raw = src.read_text(encoding="utf-8-sig", errors="replace")
        explicit_title, body = split_title(raw)
        work_id = allocate_work_id(src, existing | {r["work_id"].lower() for r in results}, i)
        title = explicit_title or (src.stem if not SAFE_ID_RE.match(src.stem) else src.stem)
        lang = detect_language(body)

        entry = {"原文件": src.name, "work_id": work_id, "标题": title, "源语言": lang,
                 "字符数": len(body), "dry_run": args.dry_run}
        if args.dry_run:
            results.append(entry)
            continue

        wdir = cfg.work_dir(work_id)
        cfg.ensure_work_dirs(work_id)
        (wdir / "source.txt").write_text(body, encoding="utf-8", newline="\n")

        # 建档 + 切块（进程内调用，避免 subprocess 的 stdio 限制）
        import init_work
        import make_chunks

        lang_block = schema.default_lang_block(lang, args.tgt_lang)
        work = init_work.build_work(work_id, title, wdir / "source.txt", cfg.output_dir(work_id), lang_block)
        init_work.write_reader(cfg.root(), work)
        init_work.update_index(cfg.root(), work)
        init_work.write_chunks(work, cfg.chunks_dir(work_id), 80)

        rows = make_chunks.load_source_rows(cfg.chunks_dir(work_id), work_id)
        order = rows.pop("__order__")
        for target, size, prefix, dir_of in (
            ("units", 100, "unit", cfg.units_dir),
            ("chunks_600", 600, "chunk_600", cfg.chunks_600_dir),
        ):
            out_dir = dir_of(work_id)
            out_dir.mkdir(parents=True, exist_ok=True)
            for n, start in enumerate(range(0, len(order), size), start=1):
                ids = order[start : start + size]
                (out_dir / f"{prefix}_{n:04d}.jsonl").write_text(
                    "\n".join(json.dumps({"id": pid, "chapter_id": rows[pid].get("chapter_id", ""),
                                          "src": schema.get_field(rows[pid], "src")}, ensure_ascii=False)
                              for pid in ids) + "\n",
                    encoding="utf-8", newline="\n")

        entry.update({
            "段落数": work["stats"]["paragraph_count"],
            "章节数": work["stats"]["chapter_count"],
            "工作单元": len(list(cfg.units_dir(work_id).glob("*.jsonl"))),
            "标注类型": work["lang"]["annotation"],
        })

        if args.auto_terms:
            try:
                import auto_terms
                picked = auto_terms.harvest_and_write(work_id, log=print, sample_chars=args.chunk_tokens)
                entry["自动术语"] = len(picked)
            except Exception as e:                                   # noqa: BLE001
                entry["自动术语"] = f"失败：{e}"

        done_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(done_dir / src.name))
        results.append(entry)
        print(json.dumps(entry, ensure_ascii=False))

    print()
    print(json.dumps({
        "导入数量": len(results),
        "收件箱": str(inbox),
        "下一步": "py -3.11 tools\\batch_run.py --concurrency 16",
        "结果": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
