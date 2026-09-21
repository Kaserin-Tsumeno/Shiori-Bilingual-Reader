from __future__ import annotations

r"""数据模型：字段定义、标注还原、语言注册表。

一、字段语义化（为多语言预留）
   旧名（中日专用）        新名（语言无关）        含义
   ja                    src                  源语言原文（纯文本）
   ja_ruby_html          src_annotated        源语言的带标注版本（注音 / 转写）
   zh                    tgt                  目标语言译文
   ruby_notes            notes                标注备注（读音存疑等）

   读取时旧名自动兼容（读旧作品不必先迁移）；写入时一律用新名。

二、标注还原（annotation reducer）
   校验的通用形式是：reduce(annotated, 标注类型) == 原文
     ruby  包裹式（日文振り仮名、中文拼音）—— 去掉 <rt> 与标签即还原
     none  无标注（英语等不需要注音的语言）—— 恒等
     insert / paren  预留：阿拉伯语元音符号属插入式、转写常写成括号注，
                     它们的"还原"不是剥标签，而是删除标注字符/括号内容。
                     目前未实现，调用即报错——留好接缝，但不假装支持。

三、语言注册表
   assets/languages.json 描述每门语言的显示名、书写方向、推荐字体、默认标注类型。
   UI 的按钮文案与字体都由它驱动，因此新增语言不需要改阅读器代码。
"""

import html
import json
import re
from pathlib import Path

# ---------- 字段 ----------
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "src": ("src", "ja"),
    "src_annotated": ("src_annotated", "ja_ruby_html"),
    "tgt": ("tgt", "zh"),
    "notes": ("notes", "ruby_notes"),
}


def get_field(row: dict, name: str, default: str = "") -> str:
    """按新名取值，自动兼容旧字段名。"""
    for key in FIELD_ALIASES.get(name, (name,)):
        val = row.get(key)
        if val:
            return str(val)
    return default


def normalize_row(row: dict) -> dict:
    """把一行产物（旧名或新名）规范化为新字段名。"""
    return {
        "id": str(row["id"]),
        "tgt": get_field(row, "tgt"),
        "src_annotated": get_field(row, "src_annotated"),
        "notes": get_field(row, "notes"),
    }


def normalize_paragraph(para: dict) -> dict:
    """段落级规范化：把旧作品数据就地转成新字段（保留其它字段不动）。"""
    out = dict(para)
    for new, olds in FIELD_ALIASES.items():
        if new in out:
            continue
        for old in olds:
            if old in out and old != new:
                out[new] = out.pop(old)
                break
    return out


def normalize_chapter(chapter: dict) -> dict:
    out = dict(chapter)
    for old, new in (("title_ja", "title_src"), ("title_zh", "title_tgt")):
        if old in out and new not in out:
            out[new] = out.pop(old)
    return out


# ---------- 标注还原 ----------
TAG_RE = re.compile(r"<[^>]+>")
RT_RE = re.compile(r"<rt[^>]*>.*?</rt>", re.I | re.S)
BR_RE = re.compile(r"<br\s*/?>", re.I)
KANJI_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")  # 不含「々」：重复符号，无独立读音
RUBY_PAIR_RE = re.compile(r"<ruby>(.*?)<rt[^>]*>(.*?)</rt></ruby>", re.S)


def reduce_ruby(value: str) -> str:
    """ruby（包裹式）：<br>→换行、去 <rt>、去标签、HTML 反转义。"""
    value = BR_RE.sub("\n", value)
    value = RT_RE.sub("", value)
    value = TAG_RE.sub("", value)
    return html.unescape(value)


def reduce_none(value: str) -> str:
    """无标注（英语等）：仅把 <br> 视为换行，便于统一存储。"""
    return html.unescape(BR_RE.sub("\n", value))


def reduce_insert(value: str) -> str:
    raise NotImplementedError(
        "插入式标注（如阿拉伯语元音符号）尚未实现；"
        "需要为它定义标注字符集与删除规则后再启用。"
    )


def reduce_paren(value: str) -> str:
    raise NotImplementedError("括号式转写（如 東京(とうきょう)）尚未实现。")


ANNOTATIONS = {
    "ruby": reduce_ruby,
    "none": reduce_none,
    "insert": reduce_insert,
    "paren": reduce_paren,
}


def reduce_annotation(value: str, annotation: str = "ruby") -> str:
    """把带标注的文本还原为可与原文比对的纯文本。"""
    fn = ANNOTATIONS.get(annotation)
    if fn is None:
        raise NotImplementedError(f"未知的标注类型：{annotation!r}（可选：{sorted(ANNOTATIONS)}）")
    return fn(value)


def kanji_gap(html: str) -> tuple[int, int, str]:
    """返回值 (汉字总数, 已被 ruby 覆盖的汉字数, 未覆盖的汉字串)。

    仅对 ruby 类标注有意义。
    """
    plain = TAG_RE.sub("", RT_RE.sub("", BR_RE.sub("\n", html)))
    covered: list[str] = []
    for m in RUBY_PAIR_RE.finditer(html):
        covered.extend(KANJI_RE.findall(TAG_RE.sub("", m.group(1))))
    uncovered = list(KANJI_RE.findall(plain))
    for ch in covered:
        if ch in uncovered:
            uncovered.remove(ch)
    return len(KANJI_RE.findall(plain)), len(covered), "".join(uncovered)


# ---------- 语言注册表 ----------
DEFAULT_LANGUAGES: dict[str, dict] = {
    "ja": {
        "name": "日本語", "label": "日文", "dir": "ltr",
        "annotation": "ruby", "font": "'Yu Mincho','Yu Gothic','Noto Serif JP',serif",
    },
    "zh-Hans": {
        "name": "简体中文", "label": "中文", "dir": "ltr",
        "annotation": "none", "font": "'Microsoft YaHei','Noto Sans SC',sans-serif",
    },
    "zh-Hant": {
        "name": "繁體中文", "label": "繁中", "dir": "ltr",
        "annotation": "none", "font": "'Microsoft JhengHei','Noto Sans TC',sans-serif",
    },
    "en": {
        "name": "English", "label": "英文", "dir": "ltr",
        "annotation": "none", "font": "Georgia,'Times New Roman',serif",
    },
    "ko": {
        "name": "한국어", "label": "韩文", "dir": "ltr",
        "annotation": "none", "font": "'Malgun Gothic','Noto Sans KR',sans-serif",
    },
}


def languages(assets_dir: Path) -> dict[str, dict]:
    """读取 assets/languages.json；缺失或损坏则回退到内置默认表。"""
    path = assets_dir / "languages.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                merged = dict(DEFAULT_LANGUAGES)
                merged.update(data)
                return merged
        except Exception:
            pass
    return dict(DEFAULT_LANGUAGES)


def lang_info(code: str, table: dict[str, dict] | None = None) -> dict:
    """取某语言的描述；未知语言回退为只带 name 的最小结构。"""
    table = table if table is not None else DEFAULT_LANGUAGES
    if code in table:
        return table[code]
    base = code.split("-")[0]
    if base in table:
        return table[base]
    return {"name": code, "label": code, "dir": "ltr", "annotation": "none", "font": "inherit"}


# ---------- 作品语言声明 ----------
def default_lang_block(src: str = "ja", tgt: str = "zh-Hans") -> dict:
    """作品的默认语言声明（work.json 的 lang 字段）。"""
    table = DEFAULT_LANGUAGES
    return {
        "src": src,
        "tgt": tgt,
        "annotation": lang_info(src, table).get("annotation", "none"),
        "views": ["side", "stack", "src", "tgt"],
    }


def read_lang_block(work: dict) -> dict:
    """读作品的 lang 声明；老作品没有该字段时按中日推断。"""
    lang = work.get("lang")
    if isinstance(lang, dict) and lang.get("src") and lang.get("tgt"):
        out = dict(lang)
        out.setdefault("annotation", lang_info(out["src"])[ "annotation"])
        out.setdefault("views", ["side", "stack", "src", "tgt"])
        return out
    return default_lang_block()


# ---------- 读音表（pronunciation lexicon） ----------
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def readings_path(root_library, work_id: str):
    return root_library / work_id / "readings.json"


def load_readings(library_dir, work_id: str) -> dict[str, dict]:
    """读取音表：{词: {"reading": 读音, "confidence": "" | "推定"}}。

    支持两种写法：
      "花子": "はなこ"
      "桜ヶ丘": {"reading": "さくらがおか", "confidence": "推定"}
    """
    data = _read_json(readings_path(library_dir, work_id), {})
    out: dict[str, dict] = {}
    if not isinstance(data, dict):
        return out
    for term, val in data.items():
        if str(term).startswith("_"):
            continue
        if isinstance(val, str):
            out[str(term)] = {"reading": val, "confidence": ""}
        elif isinstance(val, dict) and val.get("reading"):
            out[str(term)] = {
                "reading": str(val["reading"]),
                "confidence": str(val.get("confidence", "")),
            }
    return out


def readings_text(readings: dict[str, dict]) -> str:
    """把读音表渲染成提示词里的一段。"""
    if not readings:
        return "（本作品未配置读音表；人名与专名读音请按语境判断，并在 notes 里标注推定项）"
    lines = []
    for term, info in readings.items():
        note = f"（{info['confidence']}）" if info.get("confidence") else ""
        lines.append(f"  {term} → {info['reading']}{note}")
    return "\n".join(lines)


def reading_present(annotated_html: str, reading: str) -> bool:
    """判断某读音是否已出现在该段的 ruby 注音里。

    读音可含空格（如「ひがしぼうじょう むねまさ」），逐段比对即可——
    这是"防呆"校验：确保模型没有无视读音表，而非精确对齐。
    """
    rts = "".join(re.findall(r"<rt[^>]*>(.*?)</rt>", annotated_html, re.S))
    parts = [p for p in re.split(r"[\s・]+", reading) if p]
    return bool(parts) and all(p in rts for p in parts)
