from __future__ import annotations
import sys as _sys
from pathlib import Path as _KitPath

_sys.path.insert(0, str(_KitPath(__file__).resolve().parent))
import kit_config as cfg

"""批量校验 parts/ 下所有单元产物，输出汇总报告。

对每个检测到的 unit_XXXX，用 chunks_units 的原文作基准：
  - 覆盖度：产出 id 是否覆盖该单元全部原文 id
  - ruby 一致性：ja_ruby_html 剥离 ruby 后是否与原文（剥离后）完全一致
  - zh 非空、id 顺序与输入一致、无重复

用法：
  py -3.11 tools\verify_all_units.py --work-id my-novel
退出码 0 = 所有已出现单元均完整且合格；1 = 存在不完整或不合格单元。
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_unit import load_jsonl, strip_ruby_html  # noqa: E402

UNIT_RE = re.compile(r"^unit_(\d{4})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=None, help="工作根目录，默认 KIT_ROOT 或仓库根")
    parser.add_argument("--work-id", default=None, help="作品 id（也可用环境变量 WORK_ID）")
    parser.add_argument("--show-ok", action="store_true", help="列出通过单元的明细")
    args = parser.parse_args()

    work_id = cfg.resolve_work_id(args.work_id)
    units_dir = cfg.units_dir(work_id)
    parts_dir = cfg.parts_dir(work_id)
    if not parts_dir.exists():
        raise SystemExit(f"parts 目录不存在: {parts_dir}")

    groups: dict[int, list[Path]] = {}
    for path in sorted(parts_dir.glob("*.jsonl")):
        m = UNIT_RE.match(path.name)
        if not m:
            continue
        groups.setdefault(int(m.group(1)), []).append(path)

    # 同一单元可能同时存在「合并主文件」与「早期片段」：主文件是最新成品，
    # 必须优先，否则按文件名排序时旧片段会覆盖修正后的结果。
    def effective_paths(num: int) -> tuple[list[Path], bool]:
        main = parts_dir / f"unit_{num:04d}.jsonl"
        if main.exists():
            return [main], True
        return [p for p in groups[num] if p.name != f"unit_{num:04d}.jsonl"], False

    passed, failed, incomplete = [], [], []
    total_produced = 0
    total_mismatch = 0

    for num in sorted(groups):
        src_path = units_dir / f"unit_{num:04d}.jsonl"
        if not src_path.exists():
            failed.append({"unit": num, "reason": "缺少原文单元文件"})
            continue
        src = load_jsonl(src_path)
        expected = {str(r["id"]): strip_ruby_html(r["ja"]) for r in src}
        order = [str(r["id"]) for r in src]

        seen: dict[str, dict] = {}
        paths, used_main = effective_paths(num)
        for path in paths:
            for row in load_jsonl(path):
                pid = str(row.get("id", ""))
                if pid:
                    seen[pid] = row

        ids = [i for i in order if i in seen]
        missing = [i for i in order if i not in seen]
        extra = [i for i in seen if i not in expected]
        mismatch = []
        empty_zh = []
        for pid, row in seen.items():
            if pid not in expected:
                continue
            if not (row.get("zh") or "").strip():
                empty_zh.append(pid)
            rh = row.get("ja_ruby_html") or ""
            if not rh:
                mismatch.append(pid)
            elif strip_ruby_html(rh) != expected[pid]:
                mismatch.append(pid)

        total_produced += len(ids)
        total_mismatch += len(mismatch)
        entry = {
            "unit": num,
            "parts": [p.name for p in paths],
            "used_main_file": used_main,
            "expected": len(order),
            "produced": len(ids),
            "missing": len(missing),
            "extra": extra[:5],
            "empty_zh": len(empty_zh),
            "ruby_mismatch": len(mismatch),
            "ruby_mismatch_ids": mismatch[:10],
            "id_order_ok": ids == [i for i in order if i in seen],
        }
        if missing:
            entry["reason"] = "覆盖不完整"
            incomplete.append(entry)
        elif extra or empty_zh or mismatch or not entry["id_order_ok"]:
            entry["reason"] = "校验失败"
            failed.append(entry)
        else:
            passed.append(entry)

    report = {
        "units_detected": len(groups),
        "units_passed": len(passed),
        "units_incomplete": len(incomplete),
        "units_failed": len(failed),
        "paragraphs_produced": total_produced,
        "ruby_mismatch_total": total_mismatch,
    }
    if args.show_ok:
        report["passed_detail"] = passed
    if incomplete:
        report["incomplete_detail"] = incomplete
    if failed:
        report["failed_detail"] = failed
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not failed and not incomplete else 1)


if __name__ == "__main__":
    main()
