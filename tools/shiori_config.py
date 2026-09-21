from __future__ import annotations

r"""路径与配置解析：栞 · 双语阅读器的所有路径都从这里取。

设计原则（重要）
  框架与小说彻底分离：
    · 仓库里只留框架（tools / assets / docs / README），可以安全 commit、push
    · 每本小说的**全部内容**（原作、术语表、分块、译文、日志、成书）
      都放在 library/<work_id>/ 下，而 library/ 整体被 .gitignore 排除
    · 结果：备份或搬走一本书 = 拷走它那一个目录；仓库永远不会带上正文

环境变量（都可选）：
  SHIORI_ROOT        工作根目录，默认 = 本仓库根目录
  SHIORI_LIBRARY     书库目录，默认 = <root>/library
  WORK_ID            默认作品 id（等价于各脚本的 --work-id）
  SHIORI_MODEL       默认模型，默认 deepseek-flash
  SHIORI_API_URL     API 端点，默认 https://api.deepseek.com/chat/completions
  DEEPSEEK_API_KEY   API 密钥（优先）
  DSH_CREDENTIALS    DSH 凭据文件路径，默认 ~/.dsh/.credentials.yaml

单本书的目录结构（library/<work_id>/）：
  source.txt          原始日文原稿
  glossary.json       译文术语表（日文名 → 中文名，强制全书统一）
  terms.json          译名统一规则（后处理替换）
  work.json           作品数据：段落 + 章节 + 译文 + 注音（唯一权威数据）
  chunks/             原料分块（init 生成，默认 80 段/块）
  units/              工作单元（默认 100 段，生产的调度单位）
  chunks_600/         交付分块（默认 600 段）
  parts/              生产中间产物（unit_XXXX.jsonl）
  output/             成品译文（chunk_600_*.zh_ruby.jsonl）
  cache/              批次缓存（断点续跑用）
  logs/               运行日志
  <书名>.html          生成的阅读器（文件名由 reader_name 决定）

书库级：
  library/index.json  作品索引（阅读器与工具共用）
"""

import json
import os
from pathlib import Path


# ---------- 根与书库 ----------
def root() -> Path:
    env = os.environ.get("SHIORI_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def library_dir() -> Path:
    env = os.environ.get("SHIORI_LIBRARY")
    if env:
        return Path(env).expanduser().resolve()
    return root() / "library"


def index_file() -> Path:
    return library_dir() / "index.json"


def resolve_work_id(value: str | None = None) -> str:
    wid = (value or os.environ.get("WORK_ID") or "").strip()
    if not wid:
        raise SystemExit("需要指定作品：加 --work-id <id>，或设置环境变量 WORK_ID")
    return wid


# ---------- 单本书 ----------
def work_dir(work_id: str) -> Path:
    return library_dir() / work_id


def source_file(work_id: str) -> Path:
    """原作路径：library/<work_id>/source.txt（找不到时尝试目录内唯一的 txt）。"""
    d = work_dir(work_id)
    exact = d / "source.txt"
    if exact.exists():
        return exact
    if d.exists():
        cands = sorted(p for p in d.glob("*.txt") if p.name != "source.txt")
        if len(cands) == 1:
            return cands[0]
    return exact


def glossary_path(work_id: str) -> Path:
    return work_dir(work_id) / "glossary.json"


def terms_path(work_id: str) -> Path:
    return work_dir(work_id) / "terms.json"


def work_file(work_id: str) -> Path:
    return work_dir(work_id) / "work.json"


def chunks_dir(work_id: str) -> Path:
    return work_dir(work_id) / "chunks"


def units_dir(work_id: str) -> Path:
    return work_dir(work_id) / "units"


def chunks_600_dir(work_id: str) -> Path:
    return work_dir(work_id) / "chunks_600"


def parts_dir(work_id: str) -> Path:
    return work_dir(work_id) / "parts"


def output_dir(work_id: str) -> Path:
    return work_dir(work_id) / "output"


def cache_dir(work_id: str) -> Path:
    return work_dir(work_id) / "cache"


def logs_dir(work_id: str | None = None) -> Path:
    return (work_dir(work_id) / "logs") if work_id else (library_dir() / "_logs")


def ensure_work_dirs(work_id: str) -> None:
    for d in (
        work_dir(work_id),
        chunks_dir(work_id),
        units_dir(work_id),
        chunks_600_dir(work_id),
        parts_dir(work_id),
        output_dir(work_id),
        cache_dir(work_id),
        logs_dir(work_id),
    ):
        d.mkdir(parents=True, exist_ok=True)


# ---------- 配置读取 ----------
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def glossary(work_id: str) -> dict[str, str]:
    """译文术语表：日文名 → 中文名，强制统一（以 _ 开头的键视为注释）。"""
    data = _read_json(glossary_path(work_id), {})
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if not str(k).startswith("_")}


def unify_rules(work_id: str) -> list[list[str]]:
    """译名统一规则：[[错误写法, 正确写法], ...]，长串优先。"""
    rules = _read_json(terms_path(work_id), [])
    if not isinstance(rules, list):
        return []
    return [[str(a), str(b)] for pair in rules if isinstance(pair, (list, tuple)) and len(pair) == 2 for a, b in [pair]]


def glossary_text(work_id: str) -> str:
    """把术语表渲染成提示词里的一段文字。"""
    g = glossary(work_id)
    if not g:
        return "（本作品未配置术语表）"
    return " / ".join(f"{ja}→{zh}" for ja, zh in g.items())


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
    candidates = [
        root() / "config" / "credentials.yaml",          # 本仓库凭据（不入库）
        Path(cred).expanduser() if cred else None,
        Path.home() / ".dsh" / ".credentials.yaml",      # DSH 凭据
    ]
    for path in candidates:
        if not path or not path.exists():
            continue
        try:
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                if data.get("api_key"):
                    return str(data["api_key"])
                refs = data.get("refs") or {}
                if refs.get("DEEPSEEK_API_KEY"):
                    return str(refs["DEEPSEEK_API_KEY"])
        except Exception:
            continue
    raise SystemExit(
        "未找到 API 密钥。请设置环境变量 DEEPSEEK_API_KEY，"
        "或把 {api_key: sk-...} 写进 config/credentials.yaml。"
    )
