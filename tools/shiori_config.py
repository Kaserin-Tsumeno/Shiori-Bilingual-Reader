from __future__ import annotations

r"""公共配置：工作根目录、作品 id、API 凭据与模型。

所有脚本都通过本模块解析路径，因此换作品、换目录都不需要改代码。

环境变量（都可选）：
  SHIORI_ROOT            工作根目录，默认 = 本仓库根目录
  WORK_ID             默认作品 id（等价于各脚本的 --work-id）
  SHIORI_MODEL           默认模型，默认 deepseek-flash
  SHIORI_API_URL         API 端点，默认 https://api.deepseek.com/chat/completions
  DEEPSEEK_API_KEY    API 密钥（优先）
  DSH_CREDENTIALS     DSH 凭据文件路径，默认 ~/.dsh/.credentials.yaml

目录约定（均在 SHIORI_ROOT 下）：
  sources/                 原始日文稿件（txt），文件名即作品名
  config/<work_id>/glossary.json   译文术语表（强制统一译法）
  config/<work_id>/terms.json      译名统一规则（后处理替换）
  works/<work_id>.json     作品数据（段落 + 章节 + 译文 + 注音）
  chunks/<work_id>/        原稿分块（80 段/块）
  chunks_units/<work_id>/  工作单元（100 段/单元，生产调度的最小单位）
  chunks_600/<work_id>/    交付分块（600 段/块）
  translations/<work_id>/  成品译文（chunk_600_*.zh_ruby.jsonl）
  translations/<work_id>/parts/  生产中间产物（unit_*.jsonl）
  logs/                    运行日志
"""

import json
import os
from pathlib import Path


def root() -> Path:
    env = os.environ.get("SHIORI_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def resolve_work_id(value: str | None = None) -> str:
    wid = (value or os.environ.get("WORK_ID") or "").strip()
    if not wid:
        raise SystemExit("需要指定作品：加 --work-id <id>，或设置环境变量 WORK_ID")
    return wid


# ---------- 目录 ----------
def sources_dir() -> Path:
    return root() / "sources"


def source_file(work_id: str) -> Path:
    """原始日文稿件定位：sources/<work_id>.txt，或 sources/ 下名字含 work_id 的 txt。"""
    d = sources_dir()
    exact = d / f"{work_id}.txt"
    if exact.exists():
        return exact
    cands = sorted(d.glob("*.txt"))
    for c in cands:
        if work_id in c.stem:
            return c
    if len(cands) == 1:
        return cands[0]
    return exact


def config_dir(work_id: str) -> Path:
    return root() / "config" / work_id


def works_dir() -> Path:
    return root() / "works"


def work_file(work_id: str) -> Path:
    return works_dir() / f"{work_id}.json"


def chunks_dir(work_id: str) -> Path:
    return root() / "chunks" / work_id


def units_dir(work_id: str) -> Path:
    return root() / "chunks_units" / work_id


def chunks_600_dir(work_id: str) -> Path:
    return root() / "chunks_600" / work_id


def trans_dir(work_id: str) -> Path:
    return root() / "translations" / work_id


def parts_dir(work_id: str) -> Path:
    return trans_dir(work_id) / "parts"


def logs_dir() -> Path:
    return root() / "logs"


# ---------- 配置读取 ----------
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def glossary(work_id: str) -> dict[str, str]:
    """译文术语表：日文名 → 中文名，强制统一。"""
    return _read_json(config_dir(work_id) / "glossary.json", {})


def unify_rules(work_id: str) -> list[list[str]]:
    """译名统一规则：[[错误写法, 正确写法], ...]，长串优先。"""
    rules = _read_json(config_dir(work_id) / "terms.json", [])
    return [[str(a), str(b)] for a, b in rules]


# ---------- 模型与凭据 ----------
def default_model() -> str:
    return os.environ.get("SHIORI_MODEL", "deepseek-flash")


def api_url() -> str:
    return os.environ.get("SHIORI_API_URL", "https://api.deepseek.com/chat/completions")


def load_api_key() -> str:
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key
    cred = os.environ.get("DSH_CREDENTIALS")
    path = Path(cred).expanduser() if cred else Path.home() / ".dsh" / ".credentials.yaml"
    if path.exists():
        try:
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            refs = data.get("refs") or {}
            if refs.get("DEEPSEEK_API_KEY"):
                return str(refs["DEEPSEEK_API_KEY"])
        except Exception:
            pass
    raise SystemExit(
        "未找到 API 密钥。请设置环境变量 DEEPSEEK_API_KEY，"
        "或在 config/credentials.yaml 写入 {api_key: sk-...}。"
    )


def glossary_text(work_id: str) -> str:
    """把术语表渲染成提示词里的一段文字。"""
    g = glossary(work_id)
    if not g:
        return "（本作品未配置术语表）"
    return " / ".join(f"{ja}→{zh}" for ja, zh in g.items())
