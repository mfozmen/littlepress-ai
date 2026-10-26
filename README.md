# Littlepress

[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=mfozmen_littlepress-ai&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=mfozmen_littlepress-ai)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=mfozmen_littlepress-ai&metric=coverage)](https://sonarcloud.io/summary/new_code?id=mfozmen_littlepress-ai)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=mfozmen_littlepress-ai&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=mfozmen_littlepress-ai)
[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=mfozmen_littlepress-ai&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=mfozmen_littlepress-ai)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=mfozmen_littlepress-ai&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=mfozmen_littlepress-ai)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Turn a child's picture-book **draft PDF** (scanned handwriting + drawings) into a print-ready A5 book — and the matching A4 imposed booklet ready to fold and staple, generated alongside on every render. The goal is simple: **a child should feel like a real author** — their original words are preserved end-to-end.

## How it works

`littlepress` is an **interactive CLI**. You launch it, point it at a PDF draft, and it walks you through turning that draft into a finished book — asking questions when it needs your decision, never rewriting the child's story on its own.

1. You scan or export the child's draft to a PDF — one illustration + one short text per page.
2. You run `littlepress`, pick an LLM provider (or skip AI entirely), and drop it the PDF.
3. The tool extracts pages + drawings and auto-applies OCR, typo fixes, and layout choices. It then asks for the few things only you can decide: title, author, cover image. For pages without a drawing, it can generate an illustration — always with a pricing confirm before anything is spent.
4. It renders a polished A5 picture book PDF, plus the matching A4 imposed booklet for home printing — both produced on every render.

`book.json` is an intermediate checkpoint the tool writes along the way. You can also hand-edit it or hand it directly to the renderer.

## Status

What it does today:

- Reads the PDF — extracts the child's text verbatim and the embedded drawings. A blank full-page "paper" background under the real picture is skipped, and a page number the child typed is dropped because the book prints its own page numbers: a lone number on the first or last line of a page (up to one more than the page's position, since a skipped number shifts the rest) or a trailing token equal to the page's number. Nothing else in the text is touched.
- Runs an agent conversation (Claude by default; Gemini, GPT (OpenAI), and Ollama all fully supported, including tool use). The agent replies in whatever language you type in. Gemini's free tier (1.5k requests/day at time of writing) lets you try Littlepress without a credit card, and Ollama runs entirely on your machine — no key, no network.
- While the agent is thinking or a tool is running (AI cover generation, OCR, rendering), the terminal shows a spinner, a pulsing bar and the elapsed time, labelled with what's happening (English or Turkish, following your system locale or `LITTLEPRESS_LANG`). It pauses while you're asked a y/n question and disappears when the work is done.
- The agent auto-applies high-confidence edits — OCR, typo fixes, layout choices — without pausing at each step. Page text is **only** changed by `propose_typo_fix` (bounded to 3 words / 30 chars; can't funnel a rewrite) or by a verbatim user-supplied correction via `apply_text_correction`; no tool rewrites the child's story on its own.
- The agent can propose layouts for every page at once, show you a table of the whole rhythm, and auto-apply it — instead of asking per page. For surgical tweaks it still falls back to one-page-at-a-time.
- **Post-render review loop.** Right after the setup questions the app renders the book, opens the PDF in your viewer and asks: *"Which pages have issues?"* Reply in plain text with the page number and what's wrong (e.g. `page 3 text: Bir gün...` / `page 5 restore` / `page 2 hide`). The agent turns that into `apply_text_correction` (verbatim user-supplied write), `restore_page` (undo hide + re-attach the original drawing) or `hide_page` (hide a page without discarding it); whenever the draft changed, the app re-renders and asks again. Type `done` / `ok` / `none` (or `tamam` / `bitti` / `yok`) to finish.
- **Input is immutable.** `.book-gen/input/` (the mirrored source PDF) and `.book-gen/images/page-NN.*` (the per-page drawings extracted by `pdf_ingest`) are never deleted or rewritten. Any output can be regenerated from these originals at any time.
- Five cover templates: `full-bleed` (drawing covers the whole page, title on a translucent band — the default), `framed` (letterboxed drawing, title above), `portrait-frame` (drawing inside a decorative border), `title-band-top` (coloured band with the title, drawing below), and `poster` (type-only, no drawing). The agent picks one when setting the cover — guided by the `select-cover-template` skill — and you can change it any time through the conversation.
- Optional AI cover generation when the child didn't draw a cover (or wants a different one). When OpenAI (`gpt-image-2`) or Gemini (Nano Banana 2.1) is configured as the image provider, the agent can offer `generate_cover_illustration`: it proposes a prompt + a quality tier (approximate per-image prices: OpenAI low ≈ $0.01, medium ≈ $0.05, high ≈ $0.17; Gemini low ≈ $0.04, medium ≈ $0.05, high ≈ $0.12; check the provider's pricing page), shows both in a y/n confirmation so nothing is spent without your say-so, and on approval saves the PNG under `.book-gen/images/` and wires it up as the cover. The image provider is decoupled from the chat provider via `/image-model` — you can run Claude / Ollama as the chat agent and still have OpenAI or Gemini handle image generation. Chat=OpenAI and chat=Gemini sessions auto-wire the image provider from the chat key, so single-provider users don't need `/image-model`.
- Optional AI page illustrations (`generate_page_illustration`). The natural follow-up to the Samsung-Notes duplicate-text fix: once `transcribe_page` clears the screenshot-image on a page, the page renders as text-only with no picture. When OpenAI or Gemini is configured as the image provider (via the chat-key auto-wire or an explicit `/image-model openai` / `/image-model google`), the agent can propose a fresh illustration per page — same pricing tiers as the cover tool, same y/n confirm-with-price gate, same preserve-child-voice wording ("describe the scene in your own words — don't paraphrase the child's text"). Set an optional `layout` to switch the page off text-only in the same call.
- Handles PDFs whose pages are pure images (Samsung Notes exports, phone scans, Google Keep — the ones with no extractable text layer, common for Turkish handwritten drafts). The agent flags these as `[image-only]`, and the `transcribe_page` tool OCRs the text straight out of the image. Two engines behind one tool: the default `method="vision"` routes through the active LLM's vision capability (Claude 3+, GPT-4o, Gemini 1.5+, LLaVA on Ollama — every provider's message translator forwards images in its native wire format now); `method="tesseract"` uses a local pytesseract install (zero API cost, works offline, strong on typeset printed text like Turkish *matbaa yazısı* — pass `lang="tur"` for Turkish). Pick whichever's more convenient per page; the agent can switch mid-draft. The vision prompt uses a three-sentinel classifier: `<BLANK>` (empty page), `<TEXT>` (text only), or `<MIXED>` (text plus a distinct drawing), then transcribes verbatim — no model-side cleanup. `<MIXED>` pages default to `layout="text-only"` after OCR so the rendered book doesn't print the handwritten text twice (once baked into the image, once as page text); the original drawing stays on disk, and you can opt it back in during the post-render review turn with "page N show drawing" (the agent calls `choose_layout(N, "image-top")`). The OCR result auto-applies; you audit it in the post-render review turn and correct via `apply_text_correction` if anything looks wrong. On non-vision-capable models the call fails cleanly instead of silently hallucinating a transcription.
- Blank-page cleanup. Samsung Notes exports often trail two or three empty pages after the story ends. `transcribe_page` recognises them via the `<BLANK>` sentinel and the agent auto-hides those pages via `hide_page` — no confirm needed. Hidden pages are not deleted; `restore_page` brings them back with their original drawing intact.
- Colophon-page detection. After OCR finishes, Littlepress runs a single LLM round-trip across the transcribed pages to spot any page whose entire content is book metadata (colophon, credits, dedication, copyright — e.g. an `AUTHOR:... ILLUSTRATOR:...` block on a Samsung Notes page) and auto-hides those pages so they don't render as interior story pages. Non-destructive — the page text and image stay on the draft, and `restore_page` in the post-render review turn brings any false-positive page back. Skipped on offline (`NullProvider`) sessions; LLM failures are non-fatal and surfaced as a dim warning.
- **Illustration extraction from single-raster pages.** Samsung Notes / phone-scan input arrives as one PNG per page where typed text and the drawing share the canvas. After OCR classifies a page as `<MIXED>`, Littlepress runs a row-density analysis (`src/drawing_extraction.py`) to locate the largest contiguous content block — the illustration — and crops it cleanly. The rendered book gets the clean drawing under the transcribed text (`image-top` layout); no duplicate handwriting baked into the image any more. Original full-page raster stays on disk so `restore_page` in the review turn brings the original back if extraction picked the wrong region. Falls back to text-only when extraction can't find a clear drawing region (genuinely overlapping text-and-drawing pixels).
- When you load a PDF, Littlepress OCRs image-only pages automatically and classifies each (`<TEXT>` / `<MIXED>` / `<BLANK>`) before the agent conversation starts — no per-page approval prompts during ingestion.
- **Metadata prompts are deterministic — not agent-driven.** After ingestion, Littlepress asks five plain questions in order: title → author → is-this-part-of-a-series (on yes: which volume; written into the title as `<title> - <n>`) → cover (`(a)` use a page drawing / `(b)` generate with AI / `(c)` poster) → back-cover blurb (`(a)` none / `(b)` I'll write it / `(c)` draft with AI). The user's typed answers are written to the draft verbatim (preserve-child-voice — you're typing on the child's behalf). Only the two AI branches hand off to the agent; everything else is pure data collection, so nothing burns an LLM round trip. Fresh-session semantics: every run asks these unconditionally, even if you re-run on the same PDF.
- **Language first.** The setup opens with *Dil / Language?* — `1` Türkçe, `2` English, Enter keeps your system language. The five metadata questions, the agent's replies and on-screen labels then follow that choice. Other locales fall back to English. In Turkish the y/n shortcuts widen to accept `evet` / `e` / `hayır` / `h` natively. `LITTLEPRESS_LANG=en` / `LITTLEPRESS_LANG=tr` changes the Enter default.
- **Title and author from the draft.** When page one opens with a short title line (`YAVRU KEDİ 1🐱`) or a page says `Yazar: …` / `Author: …`, setup asks you to confirm them instead of asking from scratch. A trailing number is read as the series volume, so a yes also skips the series question. Answering no falls back to the normal questions.
- **Duplicate title-header strip.** Once metadata is collected, Littlepress checks whether the first non-hidden page's transcribed text starts with a header that closely matches the book title (casefold + diacritic-fold + sequence similarity, ≥ 0.8; up to 3 leading lines are tried, so a wrapped multi-line title drops as one unit). If it does — the common Samsung-Notes case where the child wrote the title at the top of their first physical page — the duplicate header is dropped so the title only appears on the cover, not again at the start of the first interior page. Edge case: when the page consisted of ONLY the header (a dedicated title page before the story starts), the page is auto-hidden so the rendered book doesn't include a blank interior page; in the post-render review turn, telling the agent "bring page N back" calls `restore_page(page=N)` to un-hide it (you supply the page number; the original drawing re-attaches automatically). Genuinely different headings (e.g. an `Author's note`) score below the threshold and stay put.
- **The app drives the flow; the model only drafts.** If you picked an AI cover, the model writes a one-line picture description and the app shows it: Enter keeps it, typing replaces it, `n` falls back to a poster cover. The cost confirm and the generation follow. An AI back-cover blurb works the same way. Rendering and the review question are code too, so the session can't stall on a model that says "I'll render now" and stops. The agent only steps in for your free-form review corrections.
- Writes exactly two PDFs per render under `.book-gen/output/`: the A5 reading copy (`<slug>.pdf`) and the A4 2-up booklet (`<slug>_A4_booklet.pdf`, ready to print double-sided, fold, and staple). Both pop open in your OS default PDF viewer so you don't have to hunt for the files: the A5 to read, the A4 booklet to print. Re-rendering overwrites the same two files in place — no versioned snapshots cluttering the directory. Use `/render` (without `--impose`) when you explicitly want the A5 alone.
- Auto-housekeeping on every render: orphan images from earlier `generate_*_illustration` retries are dropped from `.book-gen/images/`. The stable `<slug>.pdf` / `<slug>_A4_booklet.pdf` pointers, your input PDFs, and referenced cover/page images are never touched. Run it manually with `/prune` (add `--dry-run` to preview).
- Remembers what you decided: rerunning `littlepress same-draft.pdf` picks up where the last session left off instead of asking everything again. The draft PDF is mirrored into `.book-gen/input/` on first load, so you can delete the original (Downloads, Desktop, …) and the saved session still restores.

Roadmap lives in `docs/PLAN.md`.

## Install & run

Zero-install (recommended once published to PyPI):

```bash
uvx littlepress-ai           # uv does the fetch + run
pipx run littlepress-ai      # or pipx if you prefer
```

Traditional:

```bash
pip install littlepress-ai
littlepress
```

The Claude SDK and OS-keychain support are bundled by default — no optional extras to remember.

Opt-in extra for offline OCR (lets `transcribe_page` use local Tesseract instead of a cloud LLM):

```bash
pip install 'littlepress-ai[tesseract]'
```

Needs a system `tesseract` binary + trained-data packs installed separately (Windows: UB-Mannheim installer; macOS: `brew install tesseract tesseract-lang`; Linux: `apt install tesseract-ocr tesseract-ocr-tur`). Without this extra the default `method="vision"` OCR path still works on any vision-capable provider.

From a local checkout:

```bash
pip install -e '.[dev]'            # dev environment with pytest
littlepress --version
```

To update a checkout install to the latest release, run `littlepress update` (or `littlepress-ai update`). It pulls `main` and installs any new dependencies; it refuses to touch a checkout with uncommitted changes or one that isn't on `main`. Other installs get the pip command to run instead.

`littlepress help` (or `littlepress --help`) lists the command-line usage; inside the session, `/help` lists the slash commands.

DejaVu Sans is located automatically on Windows / Linux / macOS. If it cannot be found, drop `DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` into a `fonts/` folder next to `build.py`.

## Usage — interactive (primary)

Fastest path, with a PDF draft in hand:

```bash
littlepress path/to/draft.pdf
```

On first launch you'll be asked which LLM provider to use (Claude recommended). The app opens the provider's key-creation page in your browser, walks you through the steps, and saves the key securely in your OS keychain — **you only paste it once**. Subsequent launches use the saved key silently.

With a real provider active, Claude reads the draft through a `read_draft` tool and greets you in whatever language you'll type in. Say what you want, ask what you're unsure about, and the agent walks you to a finished book.

Without a PDF argument:

```bash
littlepress
```

drops you into the same shell; load a PDF later with `/load <pdf>`.

Today's slash commands (still available as escape hatches). Type `/` alone to pop up an auto-completion menu with descriptions:

| Command | What it does |
|---|---|
| `/load <pdf>` | ingest a PDF draft into the session |
| `/pages` | list pages in the loaded draft with a text preview |
| `/title [name]` | show or set the book's title |
| `/author [name]` | show or set the book's author |
| `/render [--impose] [path]` | build the A5 picture-book PDF from the loaded draft. With `--impose` also writes an A4 2-up booklet ready to print double-sided, fold, and staple. If the previous PDF is still open in a viewer that locks it (common on Windows), the viewer window is closed first so the same file is overwritten; only if it won't close (e.g. it's asking to save changes) is the render written next to it as `<title>-2.pdf`. |
| `/print` | show the in-app print + fold + staple guide for the A4 booklet (also linked from `/help` and from `render_book`'s success message). |
| `/prune [--dry-run] [--keep N]` | remove orphan images and old snapshot PDFs from `.book-gen/`. Keeps the newest `N` snapshot versions (default 3); `--dry-run` previews without deleting. Runs automatically after every versioned `/render` too. |
| `/model` | switch the active LLM provider (re-prompts for an API key if required) |
| `/image-model [openai\|google\|none]` | set or clear the image-generation provider, independent of the chat provider. Lets you run Claude as the chat agent while OpenAI or Gemini handles `generate_cover_illustration` / `generate_page_illustration`. With no argument, prints the current state. `openai` / `google` prompt for that provider's API key (validated + saved to your keychain under a separate entry from the chat key). `none` clears. The choice persists across launches via `.book-gen/session.json`. A chat=OpenAI or chat=Gemini session auto-derives an image provider from the chat key with no `/image-model` invocation needed. |
| `/logout` | forget the saved API key and drop back to offline mode |
| `/help` | show available commands |
| `/exit` | leave the session (Ctrl-D also exits) |

On first launch the shell asks which provider to use — Claude, GPT, Gemini, or Ollama. Picking a cloud provider also prompts for its API key, which is read silently (nothing echoed to the terminal) and saved to your OS keychain so you only have to paste it once. Ollama is keyless (runs on your own machine). `/logout` forgets the saved key and drops you to offline mode.

**Drag-and-drop**: you can drag a PDF file onto the terminal window — most shells paste the full path. Press Enter and Littlepress ingests it as if you'd typed `/load <path>`.

## Project layout

- `src/cli.py` — the `littlepress` command-line entry point
- `src/` — generator modules (schema, layout, PDF assembly, imposition, ingestion)
- `tests/` — pytest suite, mirrors `src/`
- `.book-gen/` — per-project state (input PDFs, generated images, output renders, session logs — all gitignored). Each run writes `.book-gen/logs/session-<timestamp>.log` with what you typed, what the agent said and every tool it ran (API keys are never logged); the last 20 are kept. Attach the latest one when reporting a bug.

## Printing the A4 booklet

Print the `_A4_booklet.pdf` on A4 paper **double-sided, flipped on the short edge**. Fold the stack in half and staple along the spine — you now have an A5 book.

The settings vary slightly per print dialog (Adobe Reader, browser PDFs, macOS Preview, manual-duplex printers like the Canon MX series). See **[`docs/printing.md`](docs/printing.md)** for a step-by-step guide with viewer-specific instructions, manual-duplex flip orientation, fold + staple steps, and an explanation of why the booklet has one blank A4 page (it's the inside-cover wrap and folds correctly — not a bug).

## Testing

The project follows **test-driven development**. Every feature and bug fix starts with a failing test.

```bash
pytest                               # full suite
pytest --cov=src --cov=build         # with coverage (reported to SonarCloud)
```

## License

MIT — see [LICENSE](LICENSE).
