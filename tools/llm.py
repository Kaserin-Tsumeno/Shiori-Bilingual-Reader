from __future__ import annotations

r"""通用 LLM 调用：给「判断类」任务用。

与 api_pipeline 的分工：
  api_pipeline  大批量、可重试、带硬校验的生产循环（翻译 + 注音）
  本模块        单次或小批量的"咨询式"调用：让模型做判断，结果写成数据文件
                （术语抽取、读音裁决、质量抽查）

设计原则：判断结果必须落成文件（glossary.json / readings.json / 报告），
而不是中断流水线等人确认——人只在想抽查时介入。
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shiori_config as cfg  # noqa: E402


class LLMError(RuntimeError):
    pass


def chat(system: str, user: str, *, model: str | None = None, temperature: float = 0.2,
         timeout: int = 900, retries: int = 3, log=None) -> str:
    """单次对话，返回文本内容。带指数退避重试。"""
    key = cfg.load_api_key()
    body = {
        "model": model or cfg.default_model(),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "stream": False,
    }
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                cfg.api_url(),
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = e.read()[:200].decode("utf-8", "ignore")
            last = f"HTTP {e.code}: {detail}"
        except Exception as e:                                   # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        if log:
            log(f"  LLM 第 {attempt} 次失败：{last}")
        time.sleep(min(2 ** attempt * 2, 20))
    raise LLMError(last or "未知错误")


def _extract_json(text: str):
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = t.find(opener), t.rfind(closer)
        if i >= 0 and j > i:
            try:
                return json.loads(t[i : j + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError("模型输出不是合法 JSON")


def chat_json(system: str, user: str, *, model: str | None = None, retries: int = 3, log=None):
    """要求模型输出 JSON 并解析；失败时追加更强的格式指令重试。"""
    note = ""
    last = None
    for attempt in range(1, retries + 1):
        text = chat(system + note, user, model=model, retries=1, log=log)
        try:
            return _extract_json(text)
        except LLMError as e:
            last = e
            if log:
                log(f"  JSON 解析失败（第 {attempt} 次）：{text[:120]!r}")
            note = "\n\n注意：只输出 JSON 本体，不要 markdown 代码块、不要任何解释文字。"
        time.sleep(1)
    raise LLMError(f"多次未能取得合法 JSON：{last}")


def chunked(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]
