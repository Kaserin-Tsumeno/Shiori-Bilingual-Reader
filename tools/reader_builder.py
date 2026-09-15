from __future__ import annotations

r"""阅读器 HTML 生成（单一来源）。

reader.html 是「数据内嵌 + 资源内联」的单文件：
  - 作品数据以 <script type="application/json"> 承载，由原生 JSON.parse 载入，
    因此 file:// 直接双击即可阅读，不需要起服务器（fetch 本地文件会被 CORS 拦）。
  - CSS / JS 从 assets/ 内联进来，改样式只需改 assets/ 再重新生成。

init_work.py 与 merge_llm_outputs.py 都调用本模块，保证模板只有一份。
"""

import json
from pathlib import Path

TEMPLATE = '''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>双语对照阅读器</title>
<style>{css}</style>
</head>
<body>
<div id="overlay" class="overlay"></div>
<div class="app">
  <aside id="sidebar" class="sidebar">
    <div class="sidebar-head">
      <div><div class="brand">双语对照阅读器</div><div class="subtle">中日对照 · 假名注音</div></div>
      <select id="workSelect" title="作品"></select>
      <div class="chapter-tools"><input id="sideSearch" type="search" placeholder="筛选章节"></div>
    </div>
    <div class="side-tabs">
      <button type="button" data-tab="chapters" class="active">目录</button>
      <button type="button" data-tab="bookmarks">书签<span class="tab-count" id="bmCount">0</span></button>
    </div>
    <nav id="chapterList" class="chapter-list" aria-label="章节目录"></nav>
    <nav id="bookmarkList" class="chapter-list" aria-label="书签列表" hidden></nav>
  </aside>
  <section class="content">
    <header class="toolbar">
      <button id="menuToggle" class="menu-toggle">目录</button>
      <select id="chapterSelect" title="章节"></select>
      <div class="segmented" aria-label="对照模式">
        <button data-layout="side" class="active">左右</button>
        <button data-layout="stack">上下</button>
        <button data-layout="ja">日文</button>
        <button data-layout="zh">中文</button>
      </div>
      <label class="toggle"><input id="rubyToggle" type="checkbox" checked> 注音</label>
      <button id="fontMinus" title="减小字号">A-</button>
      <button id="fontPlus" title="放大字号">A+</button>
      <button id="darkToggle" title="切换深浅色">暗</button>
      <input id="searchBox" type="search" placeholder="搜索日文或中文">
      <button id="prevChapter">上一章</button>
      <button id="nextChapter">下一章</button>
      <div class="menu-wrap">
        <button id="moreBtn" type="button" title="更多">⋯</button>
      </div>
      <input id="importFile" type="file" accept=".json,application/json" hidden>
      <div class="progress-track" id="progressTrack" title="全书阅读进度（点击或拖动可跳转）"><div class="progress-fill" id="progressFill"></div></div>
    </header>
    <aside id="status"></aside>
    <main id="reader" class="reader layout-side"><div class="loading-hint">正在加载…</div></main>
  </section>
</div>
<!-- 菜单必须放在 .toolbar 之外：toolbar 的 backdrop-filter 会创建包含块，
     使内部 position:fixed 相对工具栏而非视口定位（手机底部抽屉会错位）。 -->
<div id="moreMenu" class="menu" role="menu">
  <button type="button" id="expBackup">导出备份（书签 / 进度）</button>
  <button type="button" id="impBackup">导入备份…</button>
  <hr>
  <button type="button" id="expZh">导出译文（TXT）</button>
  <button type="button" id="expMd">导出双语对照（Markdown）</button>
  <hr>
  <button type="button" id="showHelp">键盘快捷键</button>
</div>
<div id="helpLayer" class="help">
  <div class="help-card">
    <h3>键盘快捷键</h3>
    <p class="tip">输入框获得焦点时，除 Esc 外快捷键不生效。</p>
    <table>
      <tr><td><kbd>←</kbd> / <kbd>→</kbd></td><td>上一章 / 下一章</td></tr>
      <tr><td><kbd>空格</kbd> / <kbd>Shift</kbd>+<kbd>空格</kbd></td><td>下翻一屏 / 上翻一屏</td></tr>
      <tr><td><kbd>Home</kbd></td><td>回到本章开头</td></tr>
      <tr><td><kbd>b</kbd></td><td>为当前段落加 / 取消书签</td></tr>
      <tr><td><kbd>/</kbd> 或 <kbd>Ctrl</kbd>+<kbd>F</kbd></td><td>聚焦搜索框</td></tr>
      <tr><td><kbd>?</kbd></td><td>显示 / 关闭本帮助</td></tr>
      <tr><td><kbd>Esc</kbd></td><td>关闭菜单 / 侧栏 / 本帮助</td></tr>
    </table>
    <button type="button" class="help-close" id="helpClose">关闭</button>
  </div>
</div>
<script id="reader-index" type="application/json">{index_json}</script>
<script id="reader-works" type="application/json">{works_json}</script>
<script>{js}</script>
</body>
</html>
'''


def _load_assets(root: Path) -> tuple[str, str]:
    css = (root / "assets" / "reader.css").read_text(encoding="utf-8")
    js = (root / "assets" / "reader.js").read_text(encoding="utf-8")
    return css, js


def build_reader_html(root: Path, work: dict) -> str:
    index_path = root / "works" / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index = {"works": [{
            "work_id": work["work_id"],
            "title": work.get("title", work["work_id"]),
            "path": f"works/{work['work_id']}.json",
        }]}
    css, js = _load_assets(root)
    # 转义 "</" 避免序列意外闭合 script 标签
    index_json = json.dumps(index, ensure_ascii=False).replace("</", "<\\/")
    works_json = json.dumps({work["work_id"]: work}, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.format(css=css, js=js, index_json=index_json, works_json=works_json)


def reader_filename(work: dict) -> str:
    """阅读器文件名：优先用作品自定的 reader_name，否则退回 work_id。

    例如 reader_name = "my-novel-双语" → 生成 "my-novel-双语.html"
    """
    name = str(work.get("reader_name") or work.get("work_id") or "reader").strip()
    for ch in '\\/:*?"<>|':          # 去掉 Windows 文件名非法字符
        name = name.replace(ch, "-")
    return f"{name}.html"


def write_reader(root: Path, work: dict) -> Path:
    path = root / reader_filename(work)
    path.write_text(build_reader_html(root, work), encoding="utf-8", newline="\n")
    legacy = root / "reader.html"
    if legacy != path:               # 清理旧入口，避免"打开的不是最新文件"
        legacy.unlink(missing_ok=True)
    return path
