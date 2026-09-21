# Shiori · Bilingual Reader

> **栞 — 双语阅读器** · Turn a Japanese novel into a Chinese translation with full furigana, in one offline HTML file.

*栞 (shiori) is the Japanese word for a bookmark — originally a wooden signpost guiding travellers along a mountain path.*

> 中文说明（主要版本）：[README.md](README.md)

---

## What it does

Give it a Japanese manuscript, get back a **bilingual book**:

- The Japanese original, **preserved character for character**
- **Furigana on every kanji** — no character left unannotated
- A faithful Simplified Chinese translation, paragraph by paragraph, nothing merged or dropped
- Side-by-side / stacked / Japanese-only / Chinese-only reading modes
- A **single HTML file** you can double-click, copy to a phone, or send to someone else

Only the translation step talks to a language model. Everything else runs locally.

---

## Layout: framework / library

```
shiori/
├─ tools/  assets/  docs/  README*.md     ← framework: tracked, safe to push
└─ library/                               ← the library: git-ignored, never pushed
   ├─ index.json
   └─ <book>/
      ├─ source.txt        the manuscript
      ├─ glossary.json     term list (keeps names consistent book-wide)
      ├─ work.json         paragraphs + chapters + translation + furigana
      ├─ chunks/ units/ chunks_600/
      ├─ parts/ output/ cache/ logs/
      └─ <book>.html       the finished book — double-click to read
```

**One book = one directory.** Everything belonging to a novel lives under
`library/<book>/`, and that whole tree is excluded by `.gitignore` — it will never be
committed or pushed. Backing up or moving a book means copying a single folder.

Point the library somewhere else (another drive, a synced folder) with:

```powershell
$env:SHIORI_LIBRARY = "E:\my-library"
```

---

## Make a book in three steps

### 1. Drop the manuscript into `library/<book>/`

```
library/your-book/source.txt     ← UTF-8, paragraphs separated by blank lines
```

Chapters are detected automatically: lines starting with 「第N話」 become chapter headings.

**Optional but recommended** — list the main character and place names in
`library/your-book/glossary.json`:

```json
{
  "山田太郎": "山田太郎",
  "鈴木花子": "铃木花子",
  "桜ヶ丘": "樱之丘"
}
```

These names are then enforced across the whole book, so a character is never
translated two different ways in two different chapters.

### 2. Generate (copy & paste)

```powershell
# 1) build the work (split paragraphs, detect chapters, create the reader skeleton)
py -3.11 tools\init_work.py --work-id your-book --title "English title"

# 2) chunk it
py -3.11 tools\make_chunks.py --work-id your-book --target units
py -3.11 tools\make_chunks.py --work-id your-book --target chunks_600

# 3) translate + annotate  (Ctrl+C any time — rerunning resumes where it stopped)
py -3.11 tools\api_pipeline.py --work-id your-book --concurrency 12

# 4) verify → merge → build the book
py -3.11 tools\verify_all_units.py --work-id your-book
py -3.11 tools\merge_parts_to_chunks.py --work-id your-book --prune-parts
py -3.11 tools\merge_llm_outputs.py --work-id your-book
```

> Step 3 is the only slow one: a ~30,000-paragraph novel takes roughly 1.5–2 hours
> at concurrency 12. You can walk away — finished units are skipped on restart.

### 3. Read it

Open **`library/<book>/<book>.html`**. That's it.

Prefer a friendlier filename? Pass `--reader-name` in step 1:

```powershell
py -3.11 tools\init_work.py --work-id your-book --title "T" --reader-name "my-novel-Bilingual"
# → produces  library/your-book/my-novel-Bilingual.html
```

---

## Using the reader

| I want to… | Do this |
|---|---|
| Change the layout | Top bar: **左右 / 上下 / 日文 / 中文** |
| Hide furigana | Uncheck **注音** (faster reading) |
| Resize text | **A-** / **A+** |
| Night mode | Click **暗** (cycles themes) |
| Jump to a chapter | Click a title in the left sidebar, use the dropdown, or **上一章 / 下一章** |
| Search | Type Japanese or Chinese in the search box, press Enter |
| Bookmark a paragraph | Hover it, click **☆** in the corner (becomes ★) |
| See bookmarks | The **书签** tab in the left sidebar |
| Resume reading | Automatic — it reopens where you left off |
| Read on a phone | Sidebar becomes a drawer; toolbar collapses; swipe to change chapters |

**Reading position and bookmarks live in your browser**, never in the work files —
so re-translating the whole book with a better model will not wipe them.

---

## Handy commands

| Command | Purpose |
|---|---|
| `unit_progress.py --work-id X --list-pending` | See which units are still missing |
| `api_pipeline.py --work-id X --units 12-20` | Redo only a few units |
| `patch_missing_ruby.py --work-id X` | Fill in furigana on stragglers |
| `unify_terms.py --work-id X` | Unify proper-noun translations (preview; add `--apply`) |
| `final_report.py --work-id X` | Pre-delivery health check |
| `audit_source_and_ruby.py --work-id X` | Check the manuscript against the work data for dropped paragraphs |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No API key found" | Set `DEEPSEEK_API_KEY`, or write `{api_key: sk-...}` to `config/credentials.yaml` |
| "No work units found" | `make_chunks` (step 2) was not run, or `--work-id` differs |
| Blank page | `Ctrl+F5`; if still blank, check the console (F12) |
| "Source not found" | The manuscript goes in `library/<book>/source.txt`, or pass `--source` |
| Some units incomplete | `unit_progress.py --list-pending`, then rerun those with `--units` |
| Furigana coverage below 100% | Run `patch_missing_ruby.py` |
| Inconsistent name translations | Add them to `library/<book>/glossary.json` and regenerate |

---

## Requirements

| Item | Requirement |
|---|---|
| Python | 3.11+ |
| Dependencies | `py -3.11 -m pip install -r requirements.txt` (just `pyyaml`) |
| Model | A DeepSeek API key (`deepseek-flash`; switch to `deepseek-v4-pro` if preferred) |
| Browser | Any modern browser. No network needed to read. |

---

## Going deeper

- **[docs/流水线详解.md](docs/流水线详解.md)** — pipeline internals: data flow, validation
  rules, retry ladder, troubleshooting (Chinese)
- Source of truth for the reader UI: `assets/reader.js`, `assets/reader.css`,
  and the single HTML template in `tools/reader_builder.py`

---

## Vocabulary

| Term | Meaning |
|---|---|
| **work** | One book, identified by a short `work_id` |
| **paragraph** | One段 of the book, id like `p00001`, stable forever |
| **unit** | A 100-paragraph working unit — the scheduling granularity |
| **chunk** | A 600-paragraph final translation file |
| **ruby** | The small kana printed above kanji (振り仮名) |
