from __future__ import annotations
import sys as _sys
from pathlib import Path as _ShioriPath

_sys.path.insert(0, str(_ShioriPath(__file__).resolve().parent))
import schema
import shiori_config as cfg

r"""术语统一：把同一专名的多种译法收敛为一种。

只改目标语言译文（tgt），绝不动源语言标注文本（src_annotated）。

用法：
  py -3.11 tools\unify_terms.py            # 预览
  py -3.11 tools\unify_terms.py --apply    # 执行
"""

import argparse
import json
import sys
from pathlib import Path

# 路径与规则来自 shiori_config


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-id", default=None)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    work_id = cfg.resolve_work_id(args.work_id)
    RULES = cfg.unify_rules(work_id)
    trans_dir = cfg.output_dir(work_id)
    files = sorted(trans_dir.glob("chunk_600_*.zh_ruby.jsonl"))
    hits: list[tuple[str, str, str]] = []
    changed_rows = 0
    changed_files = 0

    for path in files:
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        touched = False
        for r in rows:
            zh = schema.get_field(r, "tgt")
            new = zh
            for old, rep in RULES:
                if old in new:
                    new = new.replace(old, rep)
            if new != zh:
                hits.append((r["id"], zh[:60], new[:60]))
                r["tgt"] = new
                changed_rows += 1
                touched = True
        if touched:
            changed_files += 1
            if args.apply:
                path.write_text(
                    "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )

    print(json.dumps({
        "模式": "执行" if args.apply else "预览",
        "受影响段落": changed_rows,
        "受影响文件": changed_files,
    }, ensure_ascii=False, indent=2))
    for pid, before, after in hits[:15]:
        print(f"  {pid}\n    改前: {before}\n    改后: {after}")


if __name__ == "__main__":
    main()
