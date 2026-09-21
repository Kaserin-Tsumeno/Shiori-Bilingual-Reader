# 栞 · 双语阅读器

> **Shiori Bilingual Reader** — 日文小说 → 中文译文 + 假名注音 → 一部可离线打开的双语对照书

「栞」是日文里的书签，本意是山路上替行人指路的木牌。

---

## 这是什么

把一份日文原稿，做成一本**中日双语对照的电子书**：

- 日文原文**一字不改**地完整保留
- **每个汉字都带假名注音**（振り仮名），鼠标底下就是读音
- 逐段对应的简体中文译文，不漏段、不合并
- 左右对照 / 上下对照 / 只看日文 / 只看中文，随时切换
- 成品是**一个 HTML 文件**，双击就能读，也能直接发给别人

除翻译这一步需要联网调用大模型外，其余全部在本机完成。

---

## 三步做好一本书

> **一本书 = 一个目录。** 原作、术语表、中间产物、成品译文、成书全都在
> `library/<书名>/` 里，而这个目录被 `.gitignore` 整体排除——**永远不会被 commit 或 push**。
> 备份或搬走一本书，只要拷走它那一个目录。

### 第 1 步：把原稿放进 `library/<书名>/`

```
library/你的书名/source.txt      ← UTF-8 编码，段落之间用空行隔开
```

章节会自动识别：以「第N話」开头的行会被当作章节标题。

**（可选，但强烈建议）** 把主要人名写进 `library/你的书名/glossary.json`：

```json
{
  "山田太郎": "山田太郎",
  "鈴木花子": "铃木花子",
  "桜ヶ丘": "樱之丘"
}
```

写进去的名字，全书会强制统一译法，不会这一章叫「太郎」下一章叫「太郎君」。

**（强烈建议，如果你在意人名读音）** 再看一眼 `library/你的书名/readings.json`：

```json
{
  "花子": "はなこ",
  "桜ヶ丘": { "reading": "さくらがおか", "confidence": "推定" }
}
```

日语人名读音最容易被注错——同一个「田中」，模型可能这章读 たなか、那章读 でんちゅう。
读音表里的词，生成后会被**机械校验**：读音对不上就打回重做。

不知道该怎么填？先跑一次生产，再用一条命令让机器把全书读音汇总给你审：

```powershell
py -3.11 tools\harvest_readings.py --work-id 你的书名
# 产出 readings.conflicts.md —— 只列"同一个词出现多种读音"的真问题
```

### 第 2 步：生成（复制粘贴即可）

```powershell
# ① 建立作品（分段、识别章节、生成阅读器骨架）
py -3.11 tools\init_work.py --work-id 你的书名 --title "中文标题"

# ② 切块
py -3.11 tools\make_chunks.py --work-id 你的书名 --target units
py -3.11 tools\make_chunks.py --work-id 你的书名 --target chunks_600

# ③ 翻译 + 注音（可以随时 Ctrl+C，重跑会接着做）
py -3.11 tools\api_pipeline.py --work-id 你的书名 --concurrency 12

# ④ 校验 → 合并 → 生成书
py -3.11 tools\verify_all_units.py --work-id 你的书名
py -3.11 tools\merge_parts_to_chunks.py --work-id 你的书名 --prune-parts
py -3.11 tools\merge_llm_outputs.py --work-id 你的书名
```

> 第 ③ 步是唯一耗时的一步：约 3 万段的作品，12 并发大约 1.5～2 小时。
> 期间可以关掉窗口走开，它不会因为中断而白做——已完成的单元会被跳过。

### 第 3 步：阅读

打开 **`library/<你的书名>/<书名>.html`**，双击即可。

想让文件名更好认，可以在第 ① 步加上 `--reader-name`：

```powershell
py -3.11 tools\init_work.py --work-id 你的书名 --title "中文标题" --reader-name "my-novel-双语"
# → 生成 library/你的书名/my-novel-双语.html
```

---

## 目录结构：框架 / 书库 分家

```
shiori/
├─ tools/  assets/  docs/  README*.md      ← 框架：进 git，可以安全 push
│
└─ library/                                ← 书库：整体被 .gitignore 排除，永不 push
   ├─ index.json                          作品索引
   └─ <书名>/
      ├─ source.txt          原作
      ├─ glossary.json       术语表（你手写的，全书译名靠它统一）
      ├─ terms.json          译名统一规则（可选）
      ├─ work.json           作品数据：段落 + 章节 + 译文 + 注音
      ├─ chunks/ units/ chunks_600/       分块
      ├─ parts/              生产中间产物
      ├─ output/             成品译文
      ├─ cache/ logs/        缓存与日志
      └─ <书名>.html          成书（双击即读）
```

想换书库位置（比如放到另一个盘、或放进网盘同步目录），设一个环境变量即可：

```powershell
$env:SHIORI_LIBRARY = "E:\我的书库"
```

---

## 阅读器怎么用

| 想做什么 | 怎么做 |
|---|---|
| 换对照方式 | 顶部 **左右 / 上下 / 日文 / 中文** 四个按钮 |
| 收起注音 | 取消勾选 **注音**（阅读速度更快） |
| 调字号 | **A-** / **A+** |
| 夜间阅读 | 点 **暗** 切换深浅色（再次点击循环主题） |
| 跳章 | 点左侧目录任意一章；或用下拉框；或 **上一章 / 下一章** |
| 搜索 | 右上搜索框输入日文或中文，回车跳转 |
| 收藏一段 | 鼠标移到段落上，点右上角 **☆**（收藏后变 ★） |
| 看收藏 | 左侧 **书签** 页签 |
| 接着上次读 | 自动记住——下次打开直接回到上次的位置 |
| 手机上读 | 目录变成侧滑抽屉；工具栏可收纳，滑动可翻章 |

**阅读进度与书签保存在你自己的浏览器里**，不会写进作品文件。
所以以后就算重新翻译、换更好的模型重做一遍，你的书签和进度都不会丢。

---

## 常用命令

| 命令 | 用途 |
|---|---|
| `unit_progress.py --work-id X --list-pending` | 看还有哪些单元没做 |
| `api_pipeline.py --work-id X --units 12-20` | 只补做某几个单元 |
| `patch_missing_ruby.py --work-id X` | 个别汉字漏了注音，补上 |
| `unify_terms.py --work-id X` | 统一专名译法（先预览，加 `--apply` 才写入） |
| `final_report.py --work-id X` | 交付前体检（注音覆盖率、译文完整性） |
| `harvest_readings.py --work-id X` | 汇总全书读音，揪出"同词不同读"的人名 |
| `audit_source_and_ruby.py --work-id X` | 核查原稿有没有漏段 |
| `migrate_work.py --source <旧json> --work-id X` | 把旧格式作品迁到新结构 |

---

## 出问题了？

| 现象 | 怎么办 |
|---|---|
| 提示「未找到 API 密钥」 | 设环境变量 `DEEPSEEK_API_KEY`，或把 `{api_key: sk-...}` 写进 `config/credentials.yaml` |
| 提示「未找到工作单元」 | 第 ② 步的 `make_chunks` 还没跑，或 `--work-id` 和前面不一致 |
| 提示「找不到原稿」 | 原稿应放在 `library/<书名>/source.txt`，或用 `--source` 指定 |
| 打开书是空白 | `Ctrl+F5` 强制刷新；仍然空白就按 F12 看报错 |
| 某几个单元没做完 | 跑 `unit_progress.py --list-pending` 看缺口，再单独 `--units` 重跑 |
| 注音覆盖率不到 100% | 跑 `patch_missing_ruby.py` |
| 同一名字译法不统一 | 补进 `library/<书名>/glossary.json`，重新生成 |

---

## 环境要求

| 项 | 要求 |
|---|---|
| Python | 3.11 或更高 |
| 依赖 | `py -3.11 -m pip install -r requirements.txt`（只有一个 `pyyaml`） |
| 翻译模型 | DeepSeek API Key（`deepseek-flash`，可换 `deepseek-v4-pro`） |
| 浏览器 | 任意现代浏览器，不需要联网 |

---

## 想了解内部机制

- **[docs/流水线详解.md](docs/流水线详解.md)** — 数据流、校验规则、重试层级、排错
- **[docs/提示词修改指南.md](docs/提示词修改指南.md)** — 改译文风格 / 注音策略，不必动代码
- **[docs/多语言与扩展.md](docs/多语言与扩展.md)** — 加一门语言、预留了什么、没做什么
- **[docs/English](README.en.md)** — English version of this document

---

## 名词对照

| 项目中 | 指什么 |
|---|---|
| **作品 / work** | 一本书。用 `work_id`（英文短名）标识 |
| **段落 / paragraph** | 书里的一段，id 形如 `p00001`，永久不变 |
| **单元 / unit** | 100 段一组，生产的调度单位 |
| **成品 / chunk** | 600 段一块的最终译文文件 |
| **注音 / ruby** | 汉字上方的小假名（振り仮名） |
