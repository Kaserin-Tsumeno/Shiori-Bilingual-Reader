from __future__ import annotations

r"""生产管道：日译中 + ruby 假名注音（直连 DeepSeek API）。

设计要点
  - 工作单元 unit（默认 100 段）内部按 batch-size 切批并发请求
  - 每批硬校验：JSON 合法 / 行数与 id 序列 / ja_ruby_html 剥离后与原文逐字一致 / 汉字注音覆盖率 100%
  - 重试只针对不合格的行（不再整批重发），失败再降级为逐行重试
  - 最终兜底：用模型给出的注音对 + 原文切片重建 ja_ruby_html，结构上不可能改写日文
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
import kit_config as cfg  # noqa: E402

TAG_RE = re.compile(r"<[^>]+>")
RT_RE = re.compile(r"<rt[^>]*>.*?</rt>", re.I | re.S)
BR_RE = re.compile(r"<br\s*/?>", re.I)
KANJI = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")  # 不含「々」：重复符号，无独立读音

RULES_TEMPLATE = """你是资深日译中文学翻译，同时负责为日文汉字标注假名读音（ruby）。

对输入 JSONL 的每一行，输出一行严格 JSONL，字段固定为：
{{"id":"原样保留","zh":"简体中文译文","ja_ruby_html":"带 ruby 的日文原文","ruby_notes":""}}

硬性要求：
1. id 原样保留，行数与顺序与输入完全一致，不增不减、不合并、不拆分。
2. ja_ruby_html 必须逐字符保留日文原文，只允许插入 <ruby>漢字<rt>かんじ</rt></ruby>。
   - 不得改写、删减、增补、调换语序、翻译日文原文。
   - 原文换行在 JSON 字符串里写作 \\n（不要写 <br>）。
3. 【注音必须 100% 覆盖】原文中出现的每一个汉字都必须被某对 <ruby>...</ruby> 包住，一个都不能漏。
   - 送假名只给汉字部分注音：「送り」→ <ruby>送<rt>おく</rt></ruby>り
   - 假名、标点、数字、拉丁字母、片假名外来语一律不注音。
   - 连浊、熟字训、人名地名按语境判断读音。
   - 极少数无法确定的读音也必须给出推定读音，并在 ruby_notes 注明「推定」。
4. 译文：通顺自然的简体中文小说文风，准确传达原意，保留人物语气与「」引号，不总结不解释。
   译文换行位置与原文 \\n 对应。

术语表（强制）：{glossary}

只输出 JSONL 本体，不要 markdown 代码块、不要任何解释文字。"""


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


def strip_ruby(v: str) -> str:
    return TAG_RE.sub("", RT_RE.sub("", BR_RE.sub("\n", v)))


def kanji_gap(html: str) -> tuple[int, int, str]:
    """返回 (汉字总数, 已注音数, 未覆盖汉字串)。"""
    plain = TAG_RE.sub("", RT_RE.sub("", BR_RE.sub("\n", html)))
    covered: list[str] = []
    for m in re.finditer(r"<ruby>(.*?)<rt[^>]*>.*?</rt></ruby>", html, re.S):
        covered.extend(KANJI.findall(TAG_RE.sub("", m.group(1))))
    uncovered = list(KANJI.findall(plain))
    for ch in covered:
        if ch in uncovered:
            uncovered.remove(ch)
    return len(KANJI.findall(plain)), len(covered), "".join(uncovered)


def call_api(key: str, model: str, rows: list[dict], system: str, extra_note: str, timeout: int = 900) -> str:
    sys_prompt = system + ("\n\n" + extra_note if extra_note else "")
    payload = "\n".join(json.dumps({"id": r["id"], "ja": r["ja"]}, ensure_ascii=False) for r in rows)
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
    src = {r["id"]: r["ja"] for r in batch}
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
        zh = (r.get("zh") or "").strip()
        html = r.get("ja_ruby_html") or ""
        expected, actual = strip_ruby(src[pid]), strip_ruby(html)
        if actual != expected:
            bad[pid] = "改动了日文原文"
            hints.append(f"{pid}: ja_ruby_html 与原文不符。\n原文：{expected[:100]}\n你写的：{actual[:100]}")
            continue
        if not zh:
            bad[pid] = "译文为空"
            hints.append(f"{pid}: zh 不能为空。")
            continue
        total, covered, gap = kanji_gap(html)
        if total and covered < total:
            bad[pid] = f"漏注音:{gap}"
            hints.append(f"{pid}: 这些汉字没有 ruby，必须补上：{gap}")
            continue
        rows.append({"id": pid, "zh": r.get("zh", ""), "ja_ruby_html": html, "ruby_notes": r.get("ruby_notes", "")})
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
            rebuilt, gap = rebuild_ja(item["ja"], p.get("ja_ruby_html", ""))
            notes = (p.get("ruby_notes", "") + " 注音由脚本兜底重建").strip()
            if gap:
                notes += f"；未注音汉字：{gap}"
            good[item["id"]] = {"id": item["id"], "zh": p.get("zh", ""), "ja_ruby_html": rebuilt, "ruby_notes": notes}

    out = [good[r["id"]] for r in batch if r["id"] in good]
    return out if len(out) == len(batch) else None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="日译中 + ruby 注音生产管道")
    ap.add_argument("--work-id", default=None, help="作品 id（也可用环境变量 WORK_ID）")
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--model", default=None, help="默认取 KIT_MODEL 或 deepseek-flash")
    ap.add_argument("--max-try", type=int, default=4)
    ap.add_argument("--units", default=None, help="如 1-20，默认全部")
    args = ap.parse_args()

    work = cfg.resolve_work_id(args.work_id)
    model = args.model or cfg.default_model()
    units_dir = cfg.units_dir(work)
    parts_dir = cfg.parts_dir(work)
    parts_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = cfg.kit_root() / "api_out" / work / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    log = Log(cfg.logs_dir() / f"pipeline_{work}_{time.strftime('%Y%m%d_%H%M%S')}.log")

    system = RULES_TEMPLATE.format(glossary=cfg.glossary_text(work))

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

    log(f"作品 {work}｜总单元 {len(all_units)}，待处理 {len(todo)}，模型 {model}，并发 {args.concurrency}，批大小 {args.batch_size}")
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
