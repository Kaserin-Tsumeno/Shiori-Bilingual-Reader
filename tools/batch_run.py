from __future__ import annotations

r"""批量调度：把 library/ 里所有作品推到「完成」，无人值守。

每本书自动判断所处阶段并往前推：

  1. 建档    work.json 不存在            → init_work
  2. 切块    units/ 为空                 → make_chunks
  3. 判断    --auto 时：术语表 / 读音表   → auto_terms / auto_readings
  4. 生产    parts/ 不完整               → api_pipeline
  5. 合并    output/ 不完整              → merge_parts_to_chunks
  6. 出书    work.json 里仍有未译段落     → merge_llm_outputs
  7. 收尾    --finish 时：清理可再生产物   → finish_work

关键特性：
  · 书与书串行、书内并发（避免 20 本 × 12 并发打爆 API）
  · 失败隔离：某本出错只标记跳过，继续下一本，最后汇总
  · 断点续跑：直接重跑即续，已完成的阶段自动跳过
  · 全程写日志，结束生成 library/_报告.md

用法：
  py -3.11 tools\batch_run.py                          # 处理全部待办作品
  py -3.11 tools\batch_run.py --concurrency 16 --auto   # 带自动术语/读音
  py -3.11 tools\batch_run.py --only my-novel           # 只跑一本
  py -3.11 tools\batch_run.py --dry-run                 # 只看计划
"""

import argparse
import importlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402
import shiori_config as cfg  # noqa: E402


class Log:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("a", encoding="utf-8")

    def __call__(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()


def list_works(only: str | None = None) -> list[str]:
    lib = cfg.library_dir()
    if only:
        return [only]
    works = []
    idx = cfg.index_file()
    if idx.exists():
        works = [w["work_id"] for w in json.loads(idx.read_text(encoding="utf-8")).get("works", [])]
    for p in sorted(lib.iterdir()) if lib.exists() else []:
        if p.is_dir() and not p.name.startswith("_") and p.name not in works:
            works.append(p.name)
    return works


def detect_stages(work_id: str) -> list[str]:
    """判断这本书还差哪些步骤（按顺序返回）。"""
    stages = []
    if not cfg.work_file(work_id).exists() or not cfg.source_file(work_id).exists():
        stages.append("init")
        return stages                                   # 没有原稿或数据，后面无从谈起

    units = sorted(cfg.units_dir(work_id).glob("*.jsonl"))
    if not units:
        stages.append("chunks")
        return stages

    parts_dir = cfg.parts_dir(work_id)
    for u in units:
        p = parts_dir / u.name
        want = sum(1 for line in u.read_text(encoding="utf-8").splitlines() if line.strip())
        have = sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip()) if p.exists() else 0
        if have != want:
            stages.append("produce")
            break

    out_dir = cfg.output_dir(work_id)
    for c in sorted(cfg.chunks_600_dir(work_id).glob("*.jsonl")):
        if not (out_dir / f"{c.stem}.zh_ruby.jsonl").exists():
            stages.append("merge")
            break

    try:
        work = json.loads(cfg.work_file(work_id).read_text(encoding="utf-8"))
        if any(not schema.get_field(p, "tgt") for p in work.get("paragraphs", [])):
            stages.append("publish")
    except Exception:
        stages.append("publish")
    return stages


def run_script(module_name: str, argv: list[str], log: Log) -> tuple[bool, str]:
    """在进程内调用另一个脚本的 main()，避免 subprocess 的 stdio 限制。"""
    try:
        mod = importlib.import_module(module_name)
    except Exception as e:                                            # noqa: BLE001
        return False, f"导入失败 {type(e).__name__}: {e}"
    old_argv = sys.argv
    sys.argv = [f"{module_name}.py"] + argv
    try:
        mod.main()
        return True, "ok"
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 0
        return code == 0, f"SystemExit({e.code})"
    except Exception as e:                                            # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        sys.argv = old_argv


def work_summary(work_id: str) -> dict:
    """收尾统计：段数、注音覆盖率、机器做过的判断。"""
    out = {"work_id": work_id}
    wf = cfg.work_file(work_id)
    if wf.exists():
        try:
            work = json.loads(wf.read_text(encoding="utf-8"))
            paras = work.get("paragraphs", [])
            total = len(paras)
            translated = sum(1 for p in paras if schema.get_field(p, "tgt"))
            kanji = covered = 0
            for p in paras:
                html = schema.get_field(p, "src_annotated")
                if not html:
                    continue
                t, c, _ = schema.kanji_gap(html)
                kanji += t
                covered += c
            out.update({
                "段落数": total,
                "已翻译": translated,
                "注音覆盖率": f"{covered / kanji * 100:.2f}%" if kanji else "n/a",
                "章节数": len(work.get("chapters", [])),
            })
        except Exception as e:                                        # noqa: BLE001
            out["读取失败"] = str(e)

    terms = cfg.work_dir(work_id) / "terms.report.json"
    if terms.exists():
        try:
            d = json.loads(terms.read_text(encoding="utf-8"))
            out["术语表"] = d.get("累计条数", 0)
            out["术语不确定"] = d.get("不确定", [])
        except Exception:
            pass
    ver = cfg.work_dir(work_id) / "readings.verdicts.json"
    if ver.exists():
        try:
            v = json.loads(ver.read_text(encoding="utf-8"))
            out["读音裁决"] = len(v)
            out["读音低置信"] = [f"{x['term']}→{x['reading']}" for x in v if x.get("confidence") != "高"]
            out["读音回退"] = [x["term"] for x in v if not x.get("kind") and x.get("confidence") == "低"]
        except Exception:
            pass
    return out


def write_report(results: list[dict], log: Log) -> Path:
    lines = ["# 批量翻译报告", "",
             f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", "",
             "## 总览", "",
             "| 作品 | 段数 | 已译 | 注音覆盖 | 状态 | 耗时 |",
             "|---|---|---|---|---|---|"]
    for r in results:
        s = r.get("摘要", {})
        lines.append("| {w} | {n} | {t} | {c} | {st} | {d} |".format(
            w=r["work_id"], n=s.get("段落数", "-"), t=s.get("已翻译", "-"),
            c=s.get("注音覆盖率", "-"),
            st="完成" if r["ok"] else f"失败：{r.get('error', '')[:40]}",
            d=f"{r.get('seconds', 0) / 60:.1f} 分"))
    lines += ["", f"**成功 {sum(1 for r in results if r['ok'])}/{len(results)} 本**", ""]

    lines += ["## 机器做的判断（建议抽查）", ""]
    for r in results:
        s = r.get("摘要", {})
        bits = []
        if s.get("术语表"):
            bits.append(f"术语表 {s['术语表']} 条")
        if s.get("术语不确定"):
            bits.append(f"其中不确定 {len(s['术语不确定'])} 条：" + "、".join(s["术语不确定"][:8]))
        if s.get("读音裁决"):
            bits.append(f"读音裁决 {s['读音裁决']} 处")
        if s.get("读音低置信"):
            bits.append(f"低置信度 {len(s['读音低置信'])} 处：" + "、".join(s["读音低置信"][:8]))
        if s.get("读音回退"):
            bits.append(f"模型未定、回退取最高频 {len(s['读音回退'])} 处：" + "、".join(s["读音回退"][:8]))
        lines.append(f"- **{r['work_id']}**：" + ("；".join(bits) if bits else "无"))
    lines += ["", "> 这些判断已直接写入 `glossary.json` / `readings.json` 并生效，",
              "> 流水线未因等待确认而中断。如需修改，改表后重跑该作品即可。", ""]

    lines += ["## 各阶段明细", ""]
    for r in results:
        lines.append(f"### {r['work_id']}")
        for st in r.get("steps", []):
            mark = "✓" if st["ok"] else "✗"
            lines.append(f"- {mark} {st['stage']}（{st['seconds']:.1f}s）{'' if st['ok'] else ' — ' + st['detail']}")
        lines.append("")

    path = cfg.library_dir() / "_报告.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"报告已写入 {path}")
    return path


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="栞 · 批量调度（无人值守）")
    ap.add_argument("--only", default=None, help="只处理指定作品")
    ap.add_argument("--concurrency", type=int, default=12, help="每本书内部的并发")
    ap.add_argument("--model", default=None)
    ap.add_argument("--auto", action="store_true", help="自动抽取术语表与裁决读音")
    ap.add_argument("--finish", action="store_true", help="完成后清理可再生产物")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    log = Log(cfg.library_dir() / "_batch.log")
    works = list_works(args.only)
    if not works:
        log("书库里没有作品。把原稿放进 library/_inbox/ 后先跑 import_books.py")
        return

    plan = {w: detect_stages(w) for w in works}
    log(f"待处理 {len(works)} 本：" + "、".join(f"{w}({'/'.join(s) or '已完成'})" for w, s in plan.items()))
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

    results = []
    t_all = time.time()
    for i, w in enumerate(works, start=1):
        stages = plan[w]
        log(f"── [{i}/{len(works)}] {w}：{'、'.join(stages) if stages else '已完成，跳过'}")
        entry = {"work_id": w, "ok": True, "steps": [], "error": ""}
        t0 = time.time()

        if stages:
            for stage in stages:
                t_stage = time.time()
                if stage == "init":
                    ok, detail = run_script("init_work", ["--work-id", w, "--title", w], log)
                elif stage == "chunks":
                    ok, detail = run_script("make_chunks", ["--work-id", w, "--target", "units"], log)
                    if ok:
                        run_script("make_chunks", ["--work-id", w, "--target", "chunks_600"], log)
                elif stage == "produce":
                    argv = ["--work-id", w, "--concurrency", str(args.concurrency)]
                    if args.model:
                        argv += ["--model", args.model]
                    ok, detail = run_script("api_pipeline", argv, log)
                elif stage == "merge":
                    ok, detail = run_script("merge_parts_to_chunks", ["--work-id", w, "--prune-parts"], log)
                    if ok:
                        ok, detail = run_script("merge_llm_outputs", ["--work-id", w], log)
                elif stage == "publish":
                    ok, detail = run_script("merge_llm_outputs", ["--work-id", w], log)
                else:
                    ok, detail = True, "未知阶段"
                entry["steps"].append({"stage": stage, "ok": ok, "detail": detail,
                                       "seconds": time.time() - t_stage})
                if not ok:
                    entry["ok"] = False
                    entry["error"] = f"{stage}: {detail}"
                    log(f"  ✗ {stage} 失败：{detail}（跳过本书，继续下一本）")
                    break

        if args.auto and entry["ok"]:
            for stage, module, argv in (
                ("术语表", "auto_terms", ["--work-id", w]),
                ("读音表", "auto_readings", ["--work-id", w]),
            ):
                t1 = time.time()
                ok, detail = run_script(module, argv, log)
                entry["steps"].append({"stage": stage, "ok": ok, "detail": detail,
                                       "seconds": time.time() - t1})
                if not ok:
                    log(f"  ! {stage} 未完成：{detail}（不阻塞，继续）")

        if args.finish and entry["ok"]:
            run_script("finish_work", ["--work-id", w, "--level", "clean"], log)

        entry["seconds"] = time.time() - t0
        entry["摘要"] = work_summary(w)
        results.append(entry)
        log(f"  → {'完成' if entry['ok'] else '失败'}，用时 {entry['seconds']/60:.1f} 分")

    log(f"全部结束，总耗时 {(time.time()-t_all)/60:.1f} 分；"
        f"成功 {sum(1 for r in results if r['ok'])}/{len(results)}")
    write_report(results, log)

    failed = [r["work_id"] for r in results if not r["ok"]]
    if failed:
        log(f"失败作品（重跑 batch_run.py 即续）：{failed}")


if __name__ == "__main__":
    main()
