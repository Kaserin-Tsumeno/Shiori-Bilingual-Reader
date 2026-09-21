from __future__ import annotations

r"""收尾：查看 / 清理 / 归档一本书的「可再生产物」。

一本书的产物分两类：

  永久保留（不可再生）
    source.txt            原稿
    glossary.json         术语表（人工或模型判断的成果）
    readings.json         读音表
    terms.json            译名统一规则
    work.json             成品数据（段落 + 章节 + 译文 + 注音）
    <书名>.html            阅读器
    output/               成品译文

  可再生产（由 work.json 随时重建，完成后可删）
    units/  chunks_600/  chunks/  parts/  cache/

清理后一本书通常从 55–65 MB 降到 30 MB 左右。

用法：
  py -3.11 tools\finish_work.py --work-id X                 # 只看占用（默认 report）
  py -3.11 tools\finish_work.py --work-id X --level clean   # 删除可再生产物
  py -3.11 tools\finish_work.py --work-id X --level archive # 打包归档后再删
  py -3.11 tools\finish_work.py --all --level clean         # 对全部作品执行
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shiori_config as cfg  # noqa: E402

REMOVABLE = ["units", "chunks_600", "chunks", "parts", "cache"]
KEEP_FILES = ["source.txt", "glossary.json", "readings.json", "terms.json", "work.json"]


def dir_size(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    files = [p for p in path.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def survey(work_id: str) -> dict:
    wdir = cfg.work_dir(work_id)
    rows = {}
    total = 0
    for name in REMOVABLE:
        n, size = dir_size(wdir / name)
        rows[name] = {"文件数": n, "MB": round(size / 1048576, 2)}
        total += size
    for name in ("output", "logs"):
        n, size = dir_size(wdir / name)
        rows[name] = {"文件数": n, "MB": round(size / 1048576, 2)}
    for name in KEEP_FILES:
        p = wdir / name
        if p.exists():
            rows[name] = {"文件数": 1, "MB": round(p.stat().st_size / 1048576, 2)}
            total += 0
    html = list(wdir.glob("*.html"))
    if html:
        rows["<书名>.html"] = {"文件数": len(html), "MB": round(sum(p.stat().st_size for p in html) / 1048576, 2)}
    total_all = sum(p.stat().st_size for p in wdir.rglob("*") if p.is_file())
    return {"work_id": work_id, "目录": str(wdir), "构成": rows,
            "可回收MB": round(total / 1048576, 2),
            "总计MB": round(total_all / 1048576, 2)}


def archive_and_clean(work_id: str, log=print) -> dict:
    wdir = cfg.work_dir(work_id)
    arch_dir = cfg.library_dir() / "_archive"
    arch_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = arch_dir / f"{work_id}_{stamp}"
    made = []
    for name in REMOVABLE:
        src = wdir / name
        if src.exists():
            shutil.make_archive(str(base / name), "zip", root_dir=str(src.parent), base_dir=name)
            made.append(f"{name}.zip")
    (base / "manifest.json").write_text(
        json.dumps({"work_id": work_id, "归档时间": stamp, "内容": made,
                    "_说明": "解压后放回 library/<work_id>/ 即可恢复可再生产物"},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    log(f"  已归档 {len(made)} 个目录到 {base}")
    return {"归档位置": str(base), "归档项": made}


def clean(work_id: str, log=print) -> list[str]:
    wdir = cfg.work_dir(work_id)
    removed = []
    for name in REMOVABLE:
        p = wdir / name
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            removed.append(name)
    return removed


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="栞 · 收尾（查看/清理/归档）")
    ap.add_argument("--work-id", default=None)
    ap.add_argument("--all", action="store_true", help="对所有作品执行")
    ap.add_argument("--level", choices=["report", "clean", "archive"], default="report")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.all:
        index_path = cfg.index_file()
        works = []
        if index_path.exists():
            works = [w["work_id"] for w in json.loads(index_path.read_text(encoding="utf-8")).get("works", [])]
        if not works:
            works = [p.name for p in cfg.library_dir().iterdir()
                     if p.is_dir() and not p.name.startswith("_")]
    else:
        works = [cfg.resolve_work_id(args.work_id)]

    results = []
    for wid in works:
        if not cfg.work_dir(wid).exists():
            continue
        before = survey(wid)
        entry = {"work_id": wid, "清理前": before}

        if args.level == "clean" and not args.dry_run:
            entry["删除"] = clean(wid, log=print)
        elif args.level == "archive" and not args.dry_run:
            entry["归档"] = archive_and_clean(wid, log=print)
            entry["删除"] = clean(wid, log=print)

        if args.level != "report":
            entry["清理后"] = survey(wid)
            saved = entry["清理前"]["总计MB"] - entry["清理后"]["总计MB"]
            entry["回收MB"] = round(saved, 2)
        results.append(entry)

    print(json.dumps({
        "level": args.level,
        "dry_run": args.dry_run,
        "作品数": len(results),
        "结果": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
