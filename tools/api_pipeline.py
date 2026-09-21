from __future__ import annotations

r"""生产管道：日译中 + ruby 假名注音（直连 DeepSeek API）。

设计要点
  - 工作单元 unit（默认 100 段）内部按 batch-size 切批并发请求
  - 每批硬校验：JSON 合法 / 行数与 id 序列 / 标注剥离后与原文逐字一致 / 汉字注音覆盖率 100%
  - 重试只针对不合格的行（不再整批重发），失败再降级为逐行重试
  - 最终兜底：用模型给出的注音对 + 原文切片重建标注文本，结构上不可能改写日文
  - 批次级缓存：中断重启不重复消耗 token
  - 产物落在 translations/<work_id>/parts/unit_XXXX.jsonl，供 verify/merge 系列脚本使用

用法
  py -3.11 tools\api_pipeline.py --work-id my-novel --concurrency 12
  py -3.11 tools\api_pipeline.py --work-id my-novel --units 1-20     # 只跑指定单元
"""

import argparse
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402
import shiori_config as cfg  # noqa: E402

TAG_RE = re.compile(r"<[^>]+>")
RT_RE = re.compile(r"<rt[^>]*>.*?</rt>", re.I | re.S)
BR_RE = re.compile(r"<br\s*/?>", re.I)
KANJI = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")  # 不含「々」：重复符号，无独立读音

PROMPT_FILE = "prompts/translate.md"

# 外置提示词缺失时的兜底（正常情况应使用 prompts/translate.md）
FALLBACK_RULES = """你是资深日译中文学翻译，同时负责为日文汉字标注假名读音（ruby）。

对输入 JSONL 的每一行，输出一行严格 JSONL，字段固定为：
{"id":"原样保留","tgt":"译文","src_annotated":"带标注的原文","notes":""}

硬性要求：
1. id 原样保留，行数与顺序与输入完全一致，不增不减、不合并、不拆分。
2. src_annotated 必须逐字符保留原文，只允许插入 <ruby>漢字<rt>かんじ</rt></ruby>。
3. 【注音必须 100% 覆盖】原文中出现的每一个汉字都必须被某对 <ruby>...</ruby> 包住。
4. 译文：通顺自然的简体中文小说文风，不总结不解释。

术语表（强制）：[[glossary]]

读音表（强制，必须逐字采用）：[[readings]]
"""


def load_prompt_template() -> str:
    """读取外置提示词；不存在则用兜底模板。"""
    path = cfg.root() / PROMPT_FILE
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
            if text.strip():
                return text
        except Exception:
            pass
    return FALLBACK_RULES


def render_prompt(work_id: str, readings: dict) -> str:
    """把术语表与读音表填进提示词模板。"""
    return (load_prompt_template()
            .replace("[[glossary]]", cfg.glossary_text(work_id))
            .replace("[[readings]]", schema.readings_text(readings)))



class Log:
    def __init__(self, path: Path):
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("a", encoding="utf-8")

    def __call__(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        with self.lock:
            print(line, flush=True)
            self.fh.write(line + "\n")
            self.fh.flush()


# 当前作品的标注类型（main 依据作品语言设置）；一切校验都经 schema.reduce_annotation
ANNOTATION = "ruby"
READINGS: dict = {}


def strip_ruby(v: str) -> str:
    """把带标注文本还原成可与原文比对的纯文本。"""
    return schema.reduce_annotation(v, ANNOTATION)


def kanji_gap(html: str) -> tuple[int, int, str]:
    """返回值 (汉字总数, 已被 ruby 覆盖的汉字数, 未覆盖的汉字串)。"""
    return schema.kanji_gap(html)


def call_api(key: str, model: str, rows: list[dict], system: str, extra_note: str, timeout: int = 900) -> str:
    sys_prompt = system + ("\n\n" + extra_note if extra_note else "")
    payload = "\n".join(json.dumps({"id": r["id"], "src": schema.get_field(r, "src")}, ensure_ascii=False) for r in rows)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": payload},
        ],
        "temperature": 0.2,
        "stream": False,
    }
    req = urllib.request.Request(
        cfg.api_url(),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def parse_rows(text: str) -> list[dict]:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    out = []
    for line in t.split("\n"):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def judge_batch(batch: list[dict], parsed: list[dict]) -> dict:
    """逐行判定，返回 {rows: 合格行, bad: {id: 原因}, hints: [给模型的纠正提示]}。"""
    src = {r["id"]: schema.get_field(r, "src") for r in batch}
    by_id: dict[str, dict] = {}
    for r in parsed:
        pid = str(r.get("id", ""))
        if pid in src and pid not in by_id:
            by_id[pid] = r

    rows, bad, hints = [], {}, []
    for item in batch:
        pid = item["id"]
        r = by_id.get(pid)
        if r is None:
            bad[pid] = "缺失该行"
            hints.append(f"{pid}: 你漏掉了这一行，必须输出。")
            continue
        zh = schema.get_field(r, "tgt").strip()
        html = schema.get_field(r, "src_annotated")
        expected, actual = strip_ruby(src[pid]), strip_ruby(html)
        if actual != expected:
            bad[pid] = "改动了日文原文"
            hints.append(f"{pid}: src_annotated 与原文不符。\n原文：{expected[:100]}\n你写的：{actual[:100]}")
            continue
        if not zh:
            bad[pid] = "译文为空"
            hints.append(f"{pid}: zh 不能为空。")
            continue
        # 读音表校验：原文出现表内词时，该读音必须落在本段的 ruby 里
        plain_src = strip_ruby(src[pid])
        missing_reading = [t for t, info in READINGS.items()
                           if t in plain_src and not schema.reading_present(html, info["reading"])]
        if missing_reading:
            bad[pid] = "读音表未遵守:" + ",".join(missing_reading)
            hints.append(f"{pid}: 读音表要求这些词使用指定读音，请改正："
                         + "、".join(f"{t}→{READINGS[t]['reading']}" for t in missing_reading))
            continue
        total, covered, gap = kanji_gap(html)
        if total and covered < total:
            bad[pid] = f"漏注音:{gap}"
            hints.append(f"{pid}: 这些汉字没有 ruby，必须补上：{gap}")
            continue
        rows.append({"id": pid, "tgt": schema.get_field(r, "tgt"), "src_annotated": html,
                     "notes": schema.get_field(r, "notes")})
    return {"rows": rows, "bad": bad, "hints": hints}


def rebuild_ja(src_ja: str, html: str) -> tuple[str, str]:
    """兜底重建：把模型给出的 (base, rt) 注音对按顺序套回原文。

    输出完全由原文切片拼成，因此与原文逐字一致（不可能被改写）。
    """
    pairs = [(strip_ruby(b).strip(), rt) for b, rt in re.findall(r"<ruby>(.*?)<rt[^>]*>(.*?)</rt></ruby>", html, re.S)]
    out, pos = [], 0
    for base, rt in pairs:
        base = TAG_RE.sub("", base)
        if not base or not rt:
            continue
        idx = src_ja.find(base, pos)
        if idx < 0:
            continue
        out.append(src_ja[pos:idx])
        out.append(f"<ruby>{base}<rt>{TAG_RE.sub('', rt)}</rt></ruby>")
        pos = idx + len(base)
    out.append(src_ja[pos:])
    rebuilt = "".join(out)
    _, _, gap = kanji_gap(rebuilt)
    return rebuilt, gap


def run_single(ctx: dict, item: dict, max_try: int, log: Log, tag: str) -> dict | None:
    note = ""
    for _ in range(1, max_try + 1):
        try:
            text = call_api(ctx["key"], ctx["model"], [item], ctx["system"], note)
        except Exception as e:
            log(f"{tag} 单行重试异常 {type(e).__name__}: {e}")
            time.sleep(3)
            continue
        parsed = parse_rows(text)
        if not parsed:
            note = "输出无法解析，请只输出一行 JSON。"
            continue
        res = judge_batch([item], parsed)
        if res["rows"]:
            return res["rows"][0]
        note = "这一行不合格，请重做：\n" + "\n".join(res["hints"])
    return None


def run_batch(ctx: dict, batch: list[dict], max_try: int, log: Log, tag: str) -> list[dict] | None:
    """整批首轮 → 只重试不合格行 → 逐行重试 → 脚本兜底重建。"""
    good: dict[str, dict] = {}
    pending = list(batch)
    note = ""
    last: dict[str, dict] = {}

    for attempt in range(1, max_try + 1):
        try:
            text = call_api(ctx["key"], ctx["model"], pending, ctx["system"], note)
        except urllib.error.HTTPError as e:
            body = e.read()[:200].decode("utf-8", "ignore")
            log(f"{tag} 第{attempt}次 HTTP {e.code}: {body}")
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(2 ** attempt * 3, 45))
                continue
            return None
        except Exception as e:
            log(f"{tag} 第{attempt}次 异常 {type(e).__name__}: {e}")
            time.sleep(3)
            continue

        parsed = parse_rows(text)
        if not parsed:
            note = "上一轮输出无法解析为 JSONL，请严格逐行输出 JSON 对象。"
            log(f"{tag} 第{attempt}次 解析为空")
            continue

        for r in parsed:
            pid = str(r.get("id", ""))
            if pid:
                last[pid] = r

        res = judge_batch(pending, parsed)
        for row in res["rows"]:
            good[row["id"]] = row
        pending = [r for r in pending if r["id"] in res["bad"]]
        if not pending:
            break
        note = (
            f"下面 {len(pending)} 行不合格，只重新输出这些行（每行一个 JSON 对象，字段不变）。\n"
            + "\n".join(res["hints"][:20])
        )
        log(f"{tag} 第{attempt}次 有 {len(pending)} 行不合格，单独重试")

    if pending:
        log(f"{tag} 剩余 {len(pending)} 行转单行重试")
        still = []
        for item in pending:
            sub = run_single(ctx, item, 3, log, f"{tag}/single")
            if sub:
                good[item["id"]] = sub
            else:
                still.append(item)
        pending = still

    if pending:
        log(f"{tag} 仍有 {len(pending)} 行失败，启用脚本兜底重建：{[r['id'] for r in pending][:5]}")
        for item in pending:
            p = last.get(item["id"]) or {}
            rebuilt, gap = rebuild_ja(schema.get_field(item, "src"), schema.get_field(p, "src_annotated"))
            notes = (schema.get_field(p, "notes") + " 注音由脚本兜底重建").strip()
            if gap:
                notes += f"；未注音汉字：{gap}"
            good[item["id"]] = {"id": item["id"], "tgt": schema.get_field(p, "tgt"),
                                "src_annotated": rebuilt, "notes": notes}

    out = [good[r["id"]] for r in batch if r["id"] in good]
    return out if len(out) == len(batch) else None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="日译中 + ruby 注音生产管道")
    ap.add_argument("--work-id", default=None, help="作品 id（也可用环境变量 WORK_ID）")
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--model", default=None, help="默认取 SHIORI_MODEL 或 deepseek-flash")
    ap.add_argument("--max-try", type=int, default=4)
    ap.add_argument("--units", default=None, help="如 1-20，默认全部")
    args = ap.parse_args()

    work = cfg.resolve_work_id(args.work_id)
    model = args.model or cfg.default_model()

    # 标注类型由作品的语言声明决定（老作品没有 lang 字段时按日语 ruby 处理）
    lang = schema.default_lang_block()
    wf = cfg.work_file(work)
    if wf.exists():
        try:
            lang = schema.read_lang_block(json.loads(wf.read_text(encoding="utf-8")))
        except Exception:
            pass
    global ANNOTATION, READINGS
    ANNOTATION = lang.get("annotation", "ruby")
    READINGS = schema.load_readings(cfg.library_dir(), work)
    units_dir = cfg.units_dir(work)
    parts_dir = cfg.parts_dir(work)
    parts_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = cfg.root() / "api_out" / work / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    log = Log(cfg.logs_dir() / f"pipeline_{work}_{time.strftime('%Y%m%d_%H%M%S')}.log")

    system = render_prompt(work, READINGS)

    all_units = sorted(int(p.stem.split("_")[1]) for p in units_dir.glob("unit_*.jsonl"))
    if not all_units:
        raise SystemExit(f"未找到工作单元：{units_dir}\n请先运行 tools\\init_work.py 与 tools\\make_chunks.py")
    if args.units:
        if "-" in args.units:
            a, b = args.units.split("-")
            lo, hi = int(a), int(b)
        else:
            lo = hi = int(args.units)
        all_units = [u for u in all_units if lo <= u <= hi]

    todo = []
    for u in all_units:
        target = parts_dir / f"unit_{u:04d}.jsonl"
        src = units_dir / f"unit_{u:04d}.jsonl"
        src_n = sum(1 for line in src.read_text(encoding="utf-8").splitlines() if line.strip())
        if target.exists():
            done_n = sum(1 for line in target.read_text(encoding="utf-8").splitlines() if line.strip())
            if done_n == src_n:
                continue
        todo.append(u)

    log(f"作品 {work}｜总单元 {len(all_units)}，待处理 {len(todo)}，模型 {model}，并发 {args.concurrency}，批大小 {args.batch_size}，读音表 {len(READINGS)} 条，标注 {ANNOTATION}")
    if not todo:
        log("全部已完成。")
        return

    ctx = {"key": cfg.load_api_key(), "model": model, "system": system}
    t0 = time.time()
    counter = {"rows": 0}
    failed: list[int] = []
    lock = threading.Lock()

    def process_unit(u: int) -> tuple[int, bool, int]:
        src_rows = [
            json.loads(line)
            for line in (units_dir / f"unit_{u:04d}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        batches = [src_rows[i : i + args.batch_size] for i in range(0, len(src_rows), args.batch_size)]
        collected: dict[str, dict] = {}
        for bi, b in enumerate(batches):
            cache = cache_dir / f"u{u:04d}_b{bi}.jsonl"
            if cache.exists():
                try:
                    cached = [json.loads(x) for x in cache.read_text(encoding="utf-8").splitlines() if x.strip()]
                    if len(cached) == len(b) and len(judge_batch(b, cached)["rows"]) == len(b):
                        for row in cached:
                            collected[str(row["id"])] = row
                        continue
                except Exception:
                    pass
            res = run_batch(ctx, b, args.max_try, log, f"u{u:04d}/b{bi}")
            if res is None:
                continue
            cache.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in res) + "\n", encoding="utf-8", newline="\n")
            for row in res:
                collected[str(row["id"])] = row

        ordered = [collected[r["id"]] for r in src_rows if r["id"] in collected]
        if ordered:
            (parts_dir / f"unit_{u:04d}.jsonl").write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in ordered) + "\n", encoding="utf-8", newline="\n"
            )
        complete = len(ordered) == len(src_rows)
        if complete:
            for bi in range(len(batches)):
                (cache_dir / f"u{u:04d}_b{bi}.jsonl").unlink(missing_ok=True)
        return u, complete, len(ordered)

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(process_unit, u): u for u in todo}
        done = 0
        for f in as_completed(futs):
            u, complete, n = f.result()
            done += 1
            with lock:
                counter["rows"] += n
                if not complete:
                    failed.append(u)
            el = time.time() - t0
            log(
                f"进度 {done}/{len(todo)}  unit={u:04d} {'完整' if complete else '不完整'} ({n} 段)  "
                f"累计 {counter['rows']} 段  用时 {el/60:.1f}min  预计剩余 {el/done*(len(todo)-done)/60:.1f}min"
            )

    log(f"结束。完整单元 {len(todo)-len(failed)}/{len(todo)}；不完整：{failed}")
    if failed:
        (cfg.logs_dir() / f"failed_units_{work}.json").write_text(json.dumps(failed), encoding="utf-8")


if __name__ == "__main__":
    main()
