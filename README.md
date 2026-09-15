# bilingual-novel-kit

**日文小说 → 中文翻译 + 假名注音 → 双语对照阅读器** 的可复用生产框架。

给一份日文原稿（txt），产出：

- 完整准确的**简体中文译文**（逐段对应，不增删段落）
- **100% 覆盖的假名注音**（每个汉字都有 `<ruby>`，管道内硬校验）
- 一个**离线可用的双语对照阅读器**（`reader.html`，双击即用，数据内嵌）

框架本身不含任何作品数据，可以直接用来翻译下一部作品。

---

## 一、环境要求

| 项 | 要求 |
|---|---|
| Python | 3.11+ |
| 依赖 | `pyyaml`（读取 API 凭据）。其余全部为标准库 |
| API | DeepSeek（`deepseek-flash` / `deepseek-v4-pro`），需 API Key |
| 浏览器 | 任意现代浏览器（阅读器为单文件 HTML） |

安装依赖：

```powershell
py -3.11 -m pip install -r requirements.txt
```

配置 API Key（二选一）：

```powershell
# 方式 1：环境变量（推荐）
$env:DEEPSEEK_API_KEY = "sk-..."
```

或把 `{api_key: sk-...}` 写入 `config/credentials.yaml`；
若本机装过 DSH，也会自动读取 `~/.dsh/.credentials.yaml`。

---

## 二、三步走

### 第 1 步：初始化作品

把原稿放进 `sources/`，然后：

```powershell
py -3.11 tools\init_work.py `
  --source sources\my-novel.txt `
  --work-id my-novel `
  --title "我的小说标题" `
  --reader-name "my-novel-双语"
```

它会：
- 按**空行**切分段落，分配稳定 id（`p00001`、`p00002`…）
- 识别以「第N話」开头的行作为**章节标题**，分配 `c0001`…
- 生成 `works/my-novel.json`（段落 + 章节骨架）、`works/index.json`
- 生成阅读器 **`my-novel-双语.html`**（文件名取自 `--reader-name`；
  省略该参数则用 `--work-id`）。每次重新合并都会重建这个文件

### 第 2 步：（可选但推荐）配置术语表

见 `config/glossary.example.json`。把主要人名/地名/组织名写进
`config/my-novel/glossary.json`，它会注入翻译提示词，强制全书统一：

```json
{ "堀奈々々未": "堀奈奈未", "東京組長": "東京组长" }
```

> 术语表是可选的。不配也能跑，但人名翻译的一致性会差一些。

### 第 3 步：切块并生产

```powershell
# 切成 100 段一个工作单元（生产的调度单位）
py -3.11 tools\make_chunks.py --work-id my-novel --start-id p00001 --chunk-size 100 --out-subdir chunks_units --prefix unit

# 切成 600 段一个交付块（最终成品文件的分块）
py -3.11 tools\make_chunks.py --work-id my-novel --start-id p00001 --chunk-size 600 --out-subdir chunks_600 --prefix chunk_600

# 生产：翻译 + 注音（可随时 Ctrl+C，重跑会跳过已完成部分）
py -3.11 tools\api_pipeline.py --work-id my-novel --concurrency 12
```

### 第 4 步：校验 → 合并 → 出阅读器

```powershell
py -3.11 tools\verify_all_units.py --work-id my-novel          # 逐单元硬校验
py -3.11 tools\merge_parts_to_chunks.py --work-id my-novel --prune-parts
py -3.11 tools\merge_llm_outputs.py --work-id my-novel         # 重建 works/*.json 与 reader.html
py -3.11 tools\final_report.py --work-id my-novel              # 交付体检
```

打开 `reader.html` 即可阅读。

---

## 三、命令速查

| 脚本 | 作用 | 何时用 |
|---|---|---|
| `init_work.py` | 原稿 → 作品数据骨架 | 新作品开始时 |
| `make_chunks.py` | 生成工作单元 / 交付分块 | 初始化后 |
| `api_pipeline.py` | **翻译 + 注音生产** | 主要工作 |
| `unit_progress.py` | 查看生产进度、列出待办单元 | 随时 |
| `verify_unit.py` | 校验单个单元 | 抽查 |
| `verify_all_units.py` | 批量硬校验 | 每批完成后 |
| `patch_missing_ruby.py` | 补注个别漏注音的汉字 | 覆盖率不足 100% 时 |
| `merge_parts_to_chunks.py` | 单元产物 → 600 段分块成品 | 生产告一段落 |
| `merge_llm_outputs.py` | 成品 → 作品 JSON + `reader.html` | 每批合并后 |
| `unify_terms.py` | 统一专名译法（配 `terms.json`） | 定稿前 |
| `final_report.py` | 交付级体检报告 | 定稿前 |
| `audit_source_and_ruby.py` | 核查"原稿 ↔ 作品数据"是否漏段 | 存疑时 |
| `unit_task_prompt.md` | 用 **agent** 而非 API 生产时的任务规范 | 备选路线 |

---

## 四、目录结构

```
bilingual-novel-kit/
├─ sources/                  原始日文原稿（txt）
├─ config/
│  ├─ glossary.example.json  术语表模板  → 复制为 config/<work_id>/glossary.json
│  └─ terms.example.json     译名统一规则 → 复制为 config/<work_id>/terms.json
├─ tools/                    全部脚本（见上表）
│  └─ reader_builder.py      阅读器 HTML 模板（init_work 与 merge_llm_outputs 共用）
├─ assets/
│  ├─ reader.css             阅读器样式
│  └─ reader.js              阅读器逻辑（渐进渲染 + 书签 + 进度）
├─ works/
│  ├─ index.json             作品索引
│  └─ <work_id>.json         作品数据（段落+章节+译文+注音）
├─ chunks/<work_id>/         分块原料
├─ chunks_units/<work_id>/   工作单元（生产调度单位）
├─ chunks_600/<work_id>/     交付分块
├─ translations/<work_id>/   成品译文 + parts/ 中间产物
├─ logs/                     运行日志
└─ api_out/<work_id>/cache/  批次缓存（断点续跑用）
```

除 `tools/`、`assets/`、`config/*.example.json` 外的运行产物均被 `.gitignore` 排除。

---

## 五、这套框架的关键设计

### 1. 注音由模型按语境判断，但**校验是机械的**

每段产出后立刻做四重机械校验：

1. JSON 可解析
2. 行数与 id 序列与输入完全一致
3. `ja_ruby_html` 去掉 `<ruby>/<rt>` 后**与日文原文逐字符相同**（防止模型改写原文）
4. **每个汉字都被 ruby 覆盖**（不漏注音）

任一不达标 → 只把不合格的行挑出来重发（不是整批重发）→ 仍不达标则逐行重试
→ 最后兜底：用模型给的注音对 + **原文切片**重建 HTML（结构上不可能改写原文）。

### 2. 以段落 id 为唯一主线

`p00001` 这样的 id 在整条流水线里稳定不变：分块、生产、校验、合并、阅读器的
阅读进度与书签锚点，全部基于它。所以随时可以换模型重跑、重新分块，
而不会破坏已完成的工作。

### 3. 断点续跑是默认行为

- `api_pipeline.py`：已完成的 `parts/unit_XXXX.jsonl` 自动跳过；批次级缓存
  （`api_out/*/cache/`）保证中断后不重复消耗 token
- `merge_parts_to_chunks.py`：从既有成品 + parts 累积合并，重复运行结果一致

### 4. 阅读器为长篇做了优化

数万段的作品**不能一次性插入 DOM**（会白屏/卡死）。阅读器采用
**按章渐进渲染 + 滚动窗口回收**，DOM 规模恒定在几十章以内；
数据以 `<script type="application/json">` 内嵌 + 原生 `JSON.parse` 载入，
保证 `file://` 直接打开也能用（无需起服务器）。

---

### 阅读器功能一览

| 功能 | 说明 |
|---|---|
| 对照模式 | 左右 / 上下 / 仅日文 / 仅中文（窄屏自动退化为上下并置灰「左右」） |
| 假名注音 | 可开关；汉字注音覆盖率 100% |
| 目录 | 日文 + 中文双语标题，可筛选，滚动自动高亮当前章 |
| 书签 | 段落级收藏（☆ 或按 `b`），侧栏「书签」页签汇总 |
| 阅读进度 | 以**段落锚点**记录，重开自动回到原处 |
| 备份 | `⋯` 菜单导出/导入书签与进度（JSON），可跨设备迁移 |
| 导出内容 | `⋯` 菜单导出纯译文 TXT 或双语对照 Markdown |
| 键盘 | `←/→` 翻章、`空格` 翻屏、`Home` 回章首、`b` 书签、`/` 搜索、`?` 帮助、`Esc` 关闭 |
| 主题 | 跟随系统 / 常亮 / 常暗 三态循环 |
| 深链 | `文件.html#p01234` 定位到段，`#c0300` 定位到章 |
| 多端 | 桌面 / 平板 / 折叠屏展开 / 手机自适应；触屏热区 ≥40px |

> 阅读进度、书签、主题、字号都只存在浏览器本地（`localStorage`），
> 不写回 `works/*.json`——所以重跑翻译或换模型都不会冲掉你的阅读记录。

---

## 六、常见问题

**Q：生产中断了怎么办？**
直接重跑同一条命令。已完成的单元会跳过，未完成的继续。

**Q：想换模型重跑某些单元？**
删掉对应的 `translations/<work_id>/parts/unit_XXXX.jsonl`（以及 `api_out/<work_id>/cache/uXXXX_*.jsonl`），
再跑 `api_pipeline.py --units <编号>`。

**Q：注音覆盖率不是 100%？**
跑 `patch_missing_ruby.py`；它只针对漏注的行，代价极小。

**Q：阅读器打开是空白？**
先 `Ctrl+F5` 强制刷新。若仍空白，用浏览器 F12 看 Console 报错
（通常是模板里缺了 JS 引用的元素——`reader.js` 已在事件绑定处做了空值保护，
但新增控件时仍需同步 `merge_llm_outputs.py` 里的 HTML 模板）。

**Q：不想用 API，想用 agent 生产？**
用 `tools/unit_task_prompt.md` 作为 agent 任务规范，产物同样落到
`translations/<work_id>/parts/unit_XXXX.jsonl`，后续校验/合并流程完全一致。

---

## 七、成本与速度参考

以 30855 段（约 130 万日文字符）的作品为例：

| 指标 | 实测 |
|---|---|
| 模型 | `deepseek-flash` |
| 并发 | 14 |
| 耗时 | 约 104 分钟 |
| 注音覆盖率 | 100.00%（359171 / 359171 汉字） |
| ruby 还原不一致 | 0 |
| token 量 | 每 20 段约 1.3k 输入 + 9k–32k 输出 |

实际消耗取决于作品长度与重试率，可用 `--units` 先跑一个小批估算。
