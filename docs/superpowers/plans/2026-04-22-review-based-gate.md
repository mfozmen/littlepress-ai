# Review-based preserve-child-voice gate — implementation plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the preserve-child-voice `confirm` gate from "before every content mutation" to "after render, free-form review turn". Input is immutable; output is reproducible.

**Architecture:** All content-mutation tools (`propose_typo_fix`, `transcribe_page`, `propose_layouts`, `skip_page`) lose their `confirm: Callable[[str], bool]` parameter and auto-apply. `skip_page` is renamed to `hide_page` and flips a new `DraftPage.hidden` flag instead of mutating `draft.pages`. Two new tools land: `apply_text_correction` (review-turn-only, verbatim user text) and `restore_page` (undo hide / re-attach `pdf_ingest` outputs). The two AI illustration tools keep a single confirm, narrowly scoped to pricing. The REPL greeting is rewritten to drive an auto-ingest → render → "which pages have issues?" → re-render loop.

**Tech Stack:** Python 3.10+, pytest, `dataclasses`, `reportlab` + `pypdf` (unchanged), existing agent/tool-use loop.

**Spec:** `docs/superpowers/specs/2026-04-22-review-based-gate-design.md`

**Branch:** `refactor/review-based-gate`

**Contract for reviewers:** the spec is authoritative; the tasks below are the path to get there. If a task conflicts with the spec, the spec wins.

---

## File Structure

Files changed, grouped by role:

- **Schema foundation** — `src/draft.py` (add `hidden`), `src/memory.py` (schema-version bump + migration), `tests/test_draft.py`, `tests/test_memory.py`.
- **New tools** — `src/agent_tools.py` (add `apply_text_correction_tool`, `restore_page_tool`), `tests/test_agent_tools.py`.
- **Modified tools** — `src/agent_tools.py` (drop `confirm` from `propose_typo_fix_tool`, `transcribe_page_tool`, `propose_layouts_tool`; rename `skip_page_tool` → `hide_page_tool` with flag semantics; remove `keep_image` parameter path), `tests/test_agent_tools.py`.
- **REPL plumbing** — `src/repl.py` (thin the `_build_agent` tool list, rewrite `_AGENT_GREETING_HINT`), `tests/test_repl*.py`, `tests/test_agent_greeting.py`.
- **Integration** — `tests/test_review_loop.py` (new file).
- **Docs / contract** — `CLAUDE.md`, `.claude/skills/preserve-child-voice/SKILL.md`, `README.md`, `docs/PLAN.md`, `src/prune.py` (docstring addition only).

Design principles for the split:

- `src/draft.py` owns the data shape; `src/agent_tools.py` owns behaviour. Adding a field is a one-file surgical change.
- Each tool factory in `src/agent_tools.py` is a self-contained slice — modifying one doesn't ripple into others. That's what makes the per-task commits cheap.
- `src/repl.py`'s `_build_agent` is the single place wiring tools to the live session; the greeting lives one scroll above it. Updating both in the same task keeps the REPL diff readable.

---

## Chunk 1: Schema foundation

Adds `DraftPage.hidden`, teaches `to_book` to filter it out, bumps memory schema, and ensures old `draft.json` files load without error. Nothing user-visible yet.

### Task 1: Add `DraftPage.hidden` field

**Files:**
- Modify: `src/draft.py` (dataclass definition, ~line 28-33)
- Test: `tests/test_draft.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_draft.py`:

```python
def test_draft_page_defaults_to_visible():
    from src.draft import DraftPage

    page = DraftPage(text="hi")

    assert page.hidden is False


def test_draft_page_can_be_hidden():
    from src.draft import DraftPage

    page = DraftPage(text="hi", hidden=True)

    assert page.hidden is True
```

- [ ] **Step 2: Run the test**

```
pytest tests/test_draft.py::test_draft_page_defaults_to_visible tests/test_draft.py::test_draft_page_can_be_hidden -v
```

Expected: both FAIL with `TypeError: DraftPage.__init__() got an unexpected keyword argument 'hidden'` or `AttributeError`.

- [ ] **Step 3: Minimal implementation**

In `src/draft.py`, update the `DraftPage` dataclass:

```python
@dataclass
class DraftPage:
    text: str = ""
    image: Path | None = None
    layout: str = "image-top"
    hidden: bool = False
```

- [ ] **Step 4: Re-run the test**

```
pytest tests/test_draft.py -v
```

Expected: all tests pass (existing `DraftPage` tests continue to work; `hidden` defaults to `False`).

- [ ] **Step 5: Commit**

```
git add src/draft.py tests/test_draft.py
git commit -m "feat(draft): add DraftPage.hidden flag"
```

### Task 2: `to_book` filters hidden pages

**Files:**
- Modify: `src/draft.py::to_book` (~line 185-235)
- Test: `tests/test_draft.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_draft.py`:

```python
def test_to_book_excludes_hidden_pages(tmp_path):
    from src.draft import Draft, DraftPage, to_book

    draft = Draft(
        source_pdf=tmp_path / "x.pdf",
        title="Story",
        pages=[
            DraftPage(text="page 1"),
            DraftPage(text="page 2", hidden=True),
            DraftPage(text="page 3"),
        ],
    )

    book = to_book(draft, tmp_path)

    assert [p.text for p in book.pages] == ["page 1", "page 3"]
```

- [ ] **Step 2: Run to see failure**

```
pytest tests/test_draft.py::test_to_book_excludes_hidden_pages -v
```

Expected: FAIL — current `to_book` includes all three pages.

- [ ] **Step 3: Implement**

In `src/draft.py::to_book`, wrap the loop that builds `schema_pages` so hidden pages are skipped:

```python
for p in draft.pages:
    if p.hidden:
        continue
    image_str = _rel(p.image)
    layout = "text-only" if image_str is None else p.layout
    schema_pages.append(Page(text=p.text, image=image_str, layout=layout))
```

- [ ] **Step 4: Re-run all draft tests**

```
pytest tests/test_draft.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```
git add src/draft.py tests/test_draft.py
git commit -m "feat(draft): to_book filters hidden pages"
```

### Task 3: Memory schema v2 with backward-compat load

**Files:**
- Modify: `src/memory.py` (SCHEMA_VERSION, loader, writer)
- Test: `tests/test_memory.py`

Reference: `src/memory.py:45` holds `SCHEMA_VERSION = 1`. Today's loader hard-rejects any non-matching version (`src/memory.py:102`).

- [ ] **Step 1: Write the failing test (round-trip of `hidden` field)**

Append to `tests/test_memory.py`:

```python
def test_hidden_flag_round_trips_through_draft_json(tmp_path):
    from src.draft import Draft, DraftPage
    from src.memory import save_draft, load_draft

    root = tmp_path / "proj"
    root.mkdir()
    draft = Draft(
        source_pdf=root / "input.pdf",
        title="Story",
        pages=[
            DraftPage(text="visible"),
            DraftPage(text="skipped", hidden=True),
        ],
    )

    save_draft(root, draft)
    loaded = load_draft(root, source_pdf=root / "input.pdf")

    assert loaded is not None
    assert [p.hidden for p in loaded.pages] == [False, True]


def test_v1_draft_json_loads_as_all_visible(tmp_path):
    """Old .book-gen/draft.json files predate the hidden field. The
    loader must treat a missing 'hidden' key as False rather than
    blowing up, so existing projects keep working after the schema
    bump."""
    import json
    from src.memory import load_draft

    root = tmp_path / "proj"
    (root / ".book-gen").mkdir(parents=True)
    pdf = root / "input.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    (root / ".book-gen" / "draft.json").write_text(
        json.dumps(
            {
                "version": 1,
                "source_pdf": str(pdf),
                "title": "Legacy",
                "author": "",
                "cover_image": None,
                "cover_subtitle": "",
                "cover_style": "full-bleed",
                "back_cover_text": "",
                "pages": [
                    {"text": "p1", "image": None, "layout": "text-only"},
                    {"text": "p2", "image": None, "layout": "text-only"},
                ],
            }
        )
    )

    draft = load_draft(root, source_pdf=pdf)

    assert draft is not None
    assert all(not p.hidden for p in draft.pages)
```

- [ ] **Step 2: Run the tests to see failures**

```
pytest tests/test_memory.py::test_hidden_flag_round_trips_through_draft_json tests/test_memory.py::test_v1_draft_json_loads_as_all_visible -v
```

Expected: both FAIL — today's save doesn't write `hidden`; the loader rejects `version: 1` against the current schema version (or will after the bump).

- [ ] **Step 3: Implement**

In `src/memory.py`:

1. Bump the constant: `SCHEMA_VERSION = 2`.
2. Update the page-level dict produced by save and consumed by load to include `hidden` (defaulting to `False` on read if the key is absent).
3. Widen the version check so it accepts v1 and v2: v1 is read as "every page visible"; save always writes v2.

Sketch (keep the existing atomic-write machinery intact):

```python
SCHEMA_VERSION = 2
_ACCEPTED_VERSIONS = {1, 2}

def _page_to_dict(p: DraftPage) -> dict:
    return {
        "text": p.text,
        "image": str(p.image) if p.image else None,
        "layout": p.layout,
        "hidden": p.hidden,
    }

def _page_from_dict(d: dict) -> DraftPage:
    return DraftPage(
        text=d.get("text", ""),
        image=Path(d["image"]) if d.get("image") else None,
        layout=d.get("layout", "image-top"),
        hidden=bool(d.get("hidden", False)),
    )
```

And in the loader:

```python
version = data.get("version")
if version not in _ACCEPTED_VERSIONS:
    return None
```

- [ ] **Step 4: Re-run the memory tests**

```
pytest tests/test_memory.py -v
```

Expected: all pass. If any existing tests used `version: 1` literals and asserted rejection, update them to the new contract (accept v1, normalise on load).

- [ ] **Step 5: Commit**

```
git add src/memory.py tests/test_memory.py
git commit -m "feat(memory): schema v2 persists DraftPage.hidden; backward-compat reads v1"
```

---

## Chunk 2: New tools

Two new factories in `src/agent_tools.py`. Neither takes a `confirm` callback. `apply_text_correction` is review-turn-only; `restore_page` is the explicit input-preserved escape hatch.

### Task 4: `apply_text_correction_tool`

**Files:**
- Modify: `src/agent_tools.py` (new factory, registered in the module docstring's "not gated" group)
- Test: `tests/test_agent_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_tools.py`:

```python
def test_apply_text_correction_writes_verbatim(tmp_path):
    from src.agent_tools import apply_text_correction_tool
    from src.draft import Draft, DraftPage

    draft = Draft(
        source_pdf=tmp_path / "x.pdf",
        pages=[DraftPage(text="old text"), DraftPage(text="p2")],
    )
    tool = apply_text_correction_tool(get_draft=lambda: draft)

    result = tool.handler({"page": 1, "text": "Bir gün bir yumurta çatlamış"})

    assert draft.pages[0].text == "Bir gün bir yumurta çatlamış"
    assert "page 1" in result.lower()


def test_apply_text_correction_preserves_unicode_and_whitespace(tmp_path):
    from src.agent_tools import apply_text_correction_tool
    from src.draft import Draft, DraftPage

    draft = Draft(
        source_pdf=tmp_path / "x.pdf",
        pages=[DraftPage(text="old")],
    )
    tool = apply_text_correction_tool(get_draft=lambda: draft)

    payload = "İlk satır\nİkinci satır   (with trailing space) "
    tool.handler({"page": 1, "text": payload})

    assert draft.pages[0].text == payload


def test_apply_text_correction_rejects_out_of_range(tmp_path):
    from src.agent_tools import apply_text_correction_tool
    from src.draft import Draft, DraftPage

    draft = Draft(
        source_pdf=tmp_path / "x.pdf",
        pages=[DraftPage(text="p1")],
    )
    tool = apply_text_correction_tool(get_draft=lambda: draft)

    result = tool.handler({"page": 5, "text": "..."})

    assert "out of range" in result.lower()
    assert draft.pages[0].text == "p1"  # unchanged
```

- [ ] **Step 2: Run the tests**

```
pytest tests/test_agent_tools.py::test_apply_text_correction_writes_verbatim tests/test_agent_tools.py::test_apply_text_correction_preserves_unicode_and_whitespace tests/test_agent_tools.py::test_apply_text_correction_rejects_out_of_range -v
```

Expected: FAIL — `apply_text_correction_tool` doesn't exist yet.

- [ ] **Step 3: Implement the tool**

In `src/agent_tools.py`, add near the other factories. No LLM call, no prompt, no confirm — write the string as-is:

```python
def apply_text_correction_tool(get_draft: Callable[[], Draft | None]) -> Tool:
    """Tool: overwrite a page's text verbatim with a user-provided string.

    Intended for the post-render review turn: when the user says
    'page 3 text: <verbatim>', the agent calls this tool with the
    exact string. No model, no prompt, no heuristics — the incoming
    ``text`` is written straight into ``page.text``. The agent MUST
    NOT initiate this tool on its own; it is a user-initiated
    correction path.
    """

    def handler(input_: dict) -> str:
        draft = get_draft()
        if draft is None:
            return _MSG_NO_DRAFT
        page_n = int(input_["page"])
        text = input_["text"]
        if page_n < 1 or page_n > len(draft.pages):
            return (
                f"Page {page_n} is out of range — the draft has "
                f"{len(draft.pages)} pages."
            )
        draft.pages[page_n - 1].text = text
        return f"Page {page_n} text updated (verbatim, {len(text)} chars)."

    return Tool(
        name="apply_text_correction",
        description=(
            "Replace the text of page N with the user-provided string, "
            "verbatim. Use this ONLY during the post-render review turn "
            "when the user says 'page N text: ...'. Do not invent or "
            "paraphrase — the ``text`` field is written into page.text "
            "exactly as passed in. Never call on your own initiative."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "page": {"type": "integer", "minimum": 1},
                "text": {"type": "string"},
            },
            "required": ["page", "text"],
        },
        handler=handler,
    )
```

- [ ] **Step 4: Re-run**

```
pytest tests/test_agent_tools.py -k apply_text_correction -v
```

Expected: all three pass.

- [ ] **Step 5: Commit**

```
git add src/agent_tools.py tests/test_agent_tools.py
git commit -m "feat(agent): apply_text_correction tool for review-turn verbatim edits"
```

### Task 5: `restore_page_tool`

**Files:**
- Modify: `src/agent_tools.py` (new factory)
- Test: `tests/test_agent_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_tools.py`:

```python
def test_restore_page_unhides_and_resets_to_original(tmp_path):
    from PIL import Image
    from src.agent_tools import restore_page_tool
    from src.draft import Draft, DraftPage

    # Simulate pdf_ingest having laid down .book-gen/images/page-01.png
    # with the child's original drawing.
    images = tmp_path / ".book-gen" / "images"
    images.mkdir(parents=True)
    original_png = images / "page-01.png"
    Image.new("RGB", (40, 40), (10, 20, 30)).save(original_png)

    # Draft has been edited: page 1 is hidden with muddled text + cleared image.
    draft = Draft(
        source_pdf=tmp_path / ".book-gen" / "input" / "draft.pdf",
        pages=[DraftPage(text="edited", image=None, layout="text-only", hidden=True)],
    )
    tool = restore_page_tool(
        get_draft=lambda: draft,
        get_session_root=lambda: tmp_path,
    )

    result = tool.handler({"page": 1})

    assert draft.pages[0].hidden is False
    assert draft.pages[0].image == original_png
    assert "restored" in result.lower()


def test_restore_page_handles_missing_original_image(tmp_path):
    from src.agent_tools import restore_page_tool
    from src.draft import Draft, DraftPage

    draft = Draft(
        source_pdf=tmp_path / "draft.pdf",
        pages=[DraftPage(text="p1", hidden=True)],
    )
    tool = restore_page_tool(
        get_draft=lambda: draft,
        get_session_root=lambda: tmp_path,
    )

    result = tool.handler({"page": 1})

    # Unhide still happens; image stays None because there was no
    # pdf_ingest output to re-attach.
    assert draft.pages[0].hidden is False
    assert draft.pages[0].image is None
    assert "no original image" in result.lower() or "unhidden" in result.lower()
```

- [ ] **Step 2: Run to see failures**

```
pytest tests/test_agent_tools.py -k restore_page -v
```

Expected: FAIL — tool factory missing.

- [ ] **Step 3: Implement**

In `src/agent_tools.py`:

```python
def restore_page_tool(
    get_draft: Callable[[], Draft | None],
    get_session_root: Callable[[], Path],
) -> Tool:
    """Tool: undo edits on a page by re-attaching ``pdf_ingest``'s
    original output and clearing the ``hidden`` flag.

    Concrete realisation of the input-preserved guarantee:
    ``.book-gen/images/page-NN.png`` is never deleted, so the child's
    original drawing is always available to re-attach. Called when the
    user says 'page N restore' (or equivalent) during the review turn.
    Text reset to the extracted original is out of scope for this slice
    — the agent can call ``apply_text_correction`` separately if the
    user asks for a text reset.
    """

    def handler(input_: dict) -> str:
        draft = get_draft()
        if draft is None:
            return _MSG_NO_DRAFT
        page_n = int(input_["page"])
        if page_n < 1 or page_n > len(draft.pages):
            return (
                f"Page {page_n} is out of range — the draft has "
                f"{len(draft.pages)} pages."
            )
        page = draft.pages[page_n - 1]
        page.hidden = False
        original = (
            Path(get_session_root())
            / ".book-gen"
            / "images"
            / f"page-{page_n:02d}.png"
        )
        if original.is_file():
            page.image = original
            return f"Page {page_n} restored (image re-attached, unhidden)."
        return f"Page {page_n} unhidden (no original image found at {original.name})."

    return Tool(
        name="restore_page",
        description=(
            "Undo edits on page N: clear the hidden flag and re-attach "
            "the child's original drawing from pdf_ingest's per-page "
            "output (``.book-gen/images/page-NN.png``). Use when the "
            "user says 'page N restore' during the review turn. For a "
            "text reset, call apply_text_correction with the original "
            "string instead."
        ),
        input_schema={
            "type": "object",
            "properties": {"page": {"type": "integer", "minimum": 1}},
            "required": ["page"],
        },
        handler=handler,
    )
```

- [ ] **Step 4: Re-run**

```
pytest tests/test_agent_tools.py -k restore_page -v
```

Expected: both pass.

- [ ] **Step 5: Commit**

```
git add src/agent_tools.py tests/test_agent_tools.py
git commit -m "feat(agent): restore_page tool — undo hide + re-attach original drawing"
```

---

## Chunk 3: Remove confirms / rename `skip_page` → `hide_page`

Drop the per-mutation gate from the content tools. Keep the cost-only confirm on the two illustration tools. Rename `skip_page_tool` to `hide_page_tool` with flag semantics.

### Task 6: `propose_typo_fix` loses its confirm

**Files:**
- Modify: `src/agent_tools.py::propose_typo_fix_tool` (~line 258-320)
- Modify: `tests/test_agent_tools.py` — tests currently inject `confirm` fixtures
- Modify: `src/repl.py` — the factory call at ~line 307

- [ ] **Step 1: Update tests for the new signature**

In `tests/test_agent_tools.py`, find every call of `propose_typo_fix_tool(get_draft=..., confirm=...)`. Drop the `confirm` kwarg. Delete assertions that checked the confirm wording (they belong to a behaviour we're removing); keep the assertions about text mutation, bound enforcement, and reply content.

Add one new test:

```python
def test_propose_typo_fix_auto_applies_without_confirm(tmp_path):
    from src.agent_tools import propose_typo_fix_tool
    from src.draft import Draft, DraftPage

    draft = Draft(
        source_pdf=tmp_path / "x.pdf",
        pages=[DraftPage(text="Bir gün ıçinden çıktı")],
    )
    tool = propose_typo_fix_tool(get_draft=lambda: draft)  # no confirm kwarg

    tool.handler({"page": 1, "find": "ıçinden", "replace": "içinden"})

    assert draft.pages[0].text == "Bir gün içinden çıktı"
```

- [ ] **Step 2: Run updated tests to confirm they fail**

```
pytest tests/test_agent_tools.py -k propose_typo_fix -v
```

Expected: FAIL — factory signature still requires `confirm`.

- [ ] **Step 3: Update the factory**

In `src/agent_tools.py`:
- Remove `confirm: Callable[[str], bool]` from `propose_typo_fix_tool`'s parameter list.
- Inside the handler, delete the `confirm(prompt)` call and the "user declined" short-circuit.
- Keep everything else: the find/replace, the bound checks, the reply content.

- [ ] **Step 4: Run tests**

```
pytest tests/test_agent_tools.py -k propose_typo_fix -v
```

Expected: all pass.

- [ ] **Step 5: Update the REPL registration**

In `src/repl.py:307`, change the factory call:

```python
propose_typo_fix_tool(get_draft=get_draft),
```

Run the REPL-side tests:

```
pytest tests/test_repl_tools.py tests/test_agent_greeting.py -v
```

Expected: pass. If any test asserted the confirm flow at the REPL level, delete those assertions.

- [ ] **Step 6: Commit**

```
git add src/agent_tools.py src/repl.py tests/test_agent_tools.py tests/test_repl_tools.py tests/test_agent_greeting.py
git commit -m "refactor(agent): drop confirm from propose_typo_fix — auto-apply"
```

### Task 7: `propose_layouts` loses its confirm

**Files:**
- Modify: `src/agent_tools.py::propose_layouts_tool` (~line 1678-1800)
- Modify: `tests/test_agent_tools.py`
- Modify: `src/repl.py:311`

Same shape as Task 6.

- [ ] **Step 1: Update tests to the new signature; add auto-apply regression**

```python
def test_propose_layouts_auto_applies_batch(tmp_path):
    from src.agent_tools import propose_layouts_tool
    from src.draft import Draft, DraftPage

    draft = Draft(
        source_pdf=tmp_path / "x.pdf",
        pages=[
            DraftPage(text="p1", image=None),
            DraftPage(text="p2", image=tmp_path / "img2.png"),
        ],
    )
    tool = propose_layouts_tool(get_draft=lambda: draft)  # no confirm

    tool.handler(
        {
            "layouts": [
                {"page": 1, "layout": "text-only"},
                {"page": 2, "layout": "image-top"},
            ]
        }
    )

    assert draft.pages[0].layout == "text-only"
    assert draft.pages[1].layout == "image-top"
```

Delete any existing test that asserted "user said no, layouts unchanged".

- [ ] **Step 2-5: Mirror Task 6** — see that fails → drop `confirm` param + call → tests pass → update `src/repl.py:311` → commit.

Commit message: `refactor(agent): drop confirm from propose_layouts — auto-apply`.

### Task 8: `transcribe_page` — drop confirm, drop `keep_image`, add vision classification

**Files:**
- Modify: `src/agent_tools.py::transcribe_page_tool` (~line 569-760)
- Modify: `tests/test_agent_tools.py`
- Modify: `src/repl.py:327`

This is the largest single refactor in the plan. Reference — the current behaviour per spec:
- On approve, if `keep_image=False` (default): writes text, clears `page.image`, sets layout to `text-only`.
- On approve, if `keep_image=True`: writes text, leaves image and layout alone.
- On decline: nothing changes.
- `<BLANK>` sentinel: detected, surfaced in reply.

New behaviour:
- No confirm. No `keep_image` parameter. Vision prompt returns one of three sentinels:
  - `<BLANK>` — page is empty. Tool sets `page.hidden = True` (and does not touch text).
  - `<TEXT>\n<transcription>` — image is pure text (Samsung Notes / phone scan). Tool writes text, clears image, layout → `text-only`.
  - `<MIXED>\n<transcription>` — image has text AND a separate drawing. Tool writes text, keeps image, layout stays as whatever `select-page-layout` picked.
- Returns a one-line summary so the agent can narrate what happened.

- [ ] **Step 1: Write the sentinel-parse tests first**

```python
def test_transcribe_page_blank_sentinel_hides_page(tmp_path):
    from src.agent_tools import transcribe_page_tool
    from src.draft import Draft, DraftPage

    class _FakeLLM:
        def chat(self, *_a, **_kw):
            return "<BLANK>"

    draft = Draft(
        source_pdf=tmp_path / "x.pdf",
        pages=[DraftPage(text="", image=tmp_path / "p.png")],
    )
    (tmp_path / "p.png").write_bytes(b"x")
    tool = transcribe_page_tool(
        get_draft=lambda: draft,
        get_llm=lambda: _FakeLLM(),
    )

    tool.handler({"page": 1})

    assert draft.pages[0].hidden is True
    assert draft.pages[0].text == ""


def test_transcribe_page_text_sentinel_clears_image(tmp_path):
    from src.agent_tools import transcribe_page_tool
    from src.draft import Draft, DraftPage

    class _FakeLLM:
        def chat(self, *_a, **_kw):
            return "<TEXT>\nBir gün bir yumurta çatlamış"

    img = tmp_path / "p.png"
    img.write_bytes(b"x")
    draft = Draft(
        source_pdf=tmp_path / "x.pdf",
        pages=[DraftPage(text="", image=img, layout="image-top")],
    )
    tool = transcribe_page_tool(get_draft=lambda: draft, get_llm=lambda: _FakeLLM())

    tool.handler({"page": 1})

    assert draft.pages[0].text == "Bir gün bir yumurta çatlamış"
    assert draft.pages[0].image is None
    assert draft.pages[0].layout == "text-only"


def test_transcribe_page_mixed_sentinel_keeps_image(tmp_path):
    from src.agent_tools import transcribe_page_tool
    from src.draft import Draft, DraftPage

    class _FakeLLM:
        def chat(self, *_a, **_kw):
            return "<MIXED>\nKüçük dinozor ormana gitti"

    img = tmp_path / "p.png"
    img.write_bytes(b"x")
    draft = Draft(
        source_pdf=tmp_path / "x.pdf",
        pages=[DraftPage(text="", image=img, layout="image-top")],
    )
    tool = transcribe_page_tool(get_draft=lambda: draft, get_llm=lambda: _FakeLLM())

    tool.handler({"page": 1})

    assert draft.pages[0].text == "Küçük dinozor ormana gitti"
    assert draft.pages[0].image == img
    assert draft.pages[0].layout == "image-top"  # untouched


def test_transcribe_page_does_not_take_confirm_or_keep_image(tmp_path):
    """Signature regression — drop both params."""
    import inspect
    from src.agent_tools import transcribe_page_tool

    sig = inspect.signature(transcribe_page_tool)
    assert "confirm" not in sig.parameters
    assert "keep_image" not in sig.parameters
```

Delete existing tests that asserted the confirm dance or the `keep_image` parameter path.

- [ ] **Step 2: Run to see failures**

```
pytest tests/test_agent_tools.py -k transcribe_page -v
```

Expected: several FAIL.

- [ ] **Step 3: Rewrite the tool**

In `src/agent_tools.py::transcribe_page_tool`:

- Drop `confirm` and `keep_image` — both the factory signature and the handler's input schema.
- Update the vision prompt. Add the three-sentinel contract up front:

  ```
  Classify the image into exactly one of three shapes and reply accordingly:

  - If the page is empty (no meaningful content): reply with exactly
    <BLANK>
  - If the image is primarily text (handwritten or printed — a
    Samsung Notes screenshot, a scanned typed page, etc.): reply with
    <TEXT>
    <verbatim transcription, no fixes, no polishing>
  - If the image has a drawing/illustration ALONGSIDE text: reply with
    <MIXED>
    <verbatim transcription of the text only, no description of the drawing>

  Rules for the transcription: preserve the child's exact wording,
  including typos and invented words. Do not translate. Do not reorder.
  Do not explain. ONLY the sentinel line and (if applicable) the
  transcription.
  ```

- Parse the reply:
  - Strip whitespace.
  - If the first line (trimmed) is `<BLANK>`: set `page.hidden = True`, return a summary.
  - If it starts with `<TEXT>`: take everything after the first newline as the transcription, set `page.text`, clear `page.image`, set `page.layout = "text-only"`, return summary.
  - If it starts with `<MIXED>`: same as TEXT but don't clear image or change layout.
  - Otherwise (model misbehaved): fall back to treating the whole reply as `<TEXT>` content (best-effort) and return a warning in the summary.

- [ ] **Step 4: Run all transcribe tests**

```
pytest tests/test_agent_tools.py -k transcribe_page -v
```

Expected: all pass.

- [ ] **Step 5: Update the REPL registration**

In `src/repl.py:327`, drop `confirm=self._confirm` from the `transcribe_page_tool` call.

Run:

```
pytest tests/test_repl*.py -v
```

Expected: any test that asserted the `keep_image` UI or the confirm dance breaks — delete or update those assertions. Functional REPL tests should pass.

- [ ] **Step 6: Commit**

```
git add src/agent_tools.py src/repl.py tests/test_agent_tools.py tests/test_repl*.py
git commit -m "refactor(agent): transcribe_page auto-applies via <BLANK>/<TEXT>/<MIXED> sentinels"
```

### Task 9: Rename `skip_page_tool` → `hide_page_tool` with flag semantics

**Files:**
- Modify: `src/agent_tools.py::skip_page_tool` (~line 428-530)
- Modify: `tests/test_agent_tools.py`
- Modify: `src/repl.py:315`

- [ ] **Step 1: Update tests**

Rename every `skip_page_tool` reference in `tests/test_agent_tools.py` to `hide_page_tool`. Update the assertions: today `skip_page` removes from `draft.pages` and renumbers; after this change the length of `draft.pages` stays the same — `hidden` becomes `True` on the target page.

Example update:

```python
def test_hide_page_flips_hidden_flag_without_removing(tmp_path):
    from src.agent_tools import hide_page_tool
    from src.draft import Draft, DraftPage

    draft = Draft(
        source_pdf=tmp_path / "x.pdf",
        pages=[DraftPage(text="p1"), DraftPage(text="p2"), DraftPage(text="p3")],
    )
    tool = hide_page_tool(get_draft=lambda: draft)  # no confirm

    tool.handler({"page": 2})

    assert len(draft.pages) == 3
    assert draft.pages[1].hidden is True
    # Other pages unchanged.
    assert draft.pages[0].hidden is False
    assert draft.pages[2].hidden is False
```

Delete any test that asserted renumbering.

- [ ] **Step 2: Run to see failures**

```
pytest tests/test_agent_tools.py -k "hide_page or skip_page" -v
```

- [ ] **Step 3: Rewrite the tool**

In `src/agent_tools.py`:
- Rename the factory `skip_page_tool` → `hide_page_tool`.
- Drop `confirm`.
- Replace `draft.pages.pop(page_n - 1)` (and any renumbering) with `draft.pages[page_n - 1].hidden = True`.
- Update the tool's `name=` to `"hide_page"`.
- Update the description: *"Mark page N as hidden so it doesn't render. Input is preserved — `restore_page` reverses this."*

- [ ] **Step 4: Run tests**

```
pytest tests/test_agent_tools.py -k "hide_page or skip_page" -v
```

Expected: pass.

- [ ] **Step 5: Update REPL**

In `src/repl.py`:
- Change the import to `hide_page_tool`.
- Change the factory call at ~line 315 to `hide_page_tool(get_draft=get_draft)` — no confirm.

Run:

```
pytest tests/test_repl*.py -v
```

- [ ] **Step 6: Commit**

```
git add src/agent_tools.py src/repl.py tests/test_agent_tools.py tests/test_repl*.py
git commit -m "refactor(agent): rename skip_page to hide_page — flag semantics, no confirm"
```

---

## Chunk 4: REPL plumbing + greeting

Thin the `_build_agent` wiring, rewrite the greeting around the new flow.

### Task 10: Narrow `_confirm` to cost-only + clean plumbing

**Files:**
- Modify: `src/repl.py::_build_agent` (~line 302-355), `Repl._confirm` (~line 357+)
- Test: `tests/test_repl_tools.py`

After Tasks 6-9, the only callers of `self._confirm` left are `generate_cover_illustration_tool` and `generate_page_illustration_tool`. The name / docstring should reflect that narrower role.

- [ ] **Step 1: Test**

Add to `tests/test_repl_tools.py`:

```python
def test_confirm_plumbing_only_wired_to_cost_tools():
    """After the gate refactor, _confirm only gates cost-incurring
    illustration calls. Regression: if a future PR re-adds confirm to
    a content tool, the test fails so someone reconsiders."""
    import inspect
    from src import repl

    src = inspect.getsource(repl._build_agent if hasattr(repl, "_build_agent") else repl.Repl._build_agent)

    # The two cost tools still take confirm.
    assert "generate_cover_illustration_tool" in src
    assert "generate_page_illustration_tool" in src
    # The content tools do not.
    assert "propose_typo_fix_tool(get_draft=get_draft, confirm" not in src
    assert "propose_layouts_tool(get_draft=get_draft, confirm" not in src
    assert "transcribe_page_tool(" in src and "confirm=self._confirm" not in src.split("transcribe_page_tool(")[1].split(")")[0]
```

(Crude but effective — a textual regression guard.)

- [ ] **Step 2-4: Run tests; fix `_build_agent` until green.**

At this point the content tools shouldn't be passing confirm anyway (that was Tasks 6-9). Confirm only stays on the two illustration factories. Update `Repl._confirm`'s docstring to say *"asked only for cost-incurring AI illustration calls; content mutations run without a gate"*.

- [ ] **Step 5: Commit**

```
git add src/repl.py tests/test_repl_tools.py
git commit -m "refactor(repl): narrow _confirm plumbing to cost-only illustration gates"
```

### Task 11: Rewrite `_AGENT_GREETING_HINT` for the review-based flow

**Files:**
- Modify: `src/repl.py:106-157` (the greeting constant)
- Test: `tests/test_agent_greeting.py`

- [ ] **Step 1: Write the greeting test**

The greeting is a long string; assert the key behaviours are mentioned rather than exact wording.

```python
def test_greeting_drives_auto_ingest_then_review_turn():
    from src.repl import _AGENT_GREETING_HINT

    g = _AGENT_GREETING_HINT.lower()

    # Auto-ingest: OCR + typo + blank + layout happen without asking.
    assert "transcribe" in g and "without asking" in g or "no confirm" in g or "auto" in g
    # Review turn: render first, then ask which pages have issues.
    assert "render" in g and "issues" in g
    # Numeric-first ask.
    assert "page number" in g or "which pages" in g
    # Exit tokens explicitly named.
    for token in ("none", "yok", "ok", "ship"):
        assert token in g, f"review-loop exit token {token!r} missing from greeting"


def test_greeting_no_longer_has_metadata_review_checkpoint():
    """P5's 'summarise metadata, get approval before render_book'
    paragraph is subsumed by the review turn."""
    from src.repl import _AGENT_GREETING_HINT

    # Any of these phrases pointed at the old checkpoint.
    forbidden = [
        "summarise the metadata",
        "approve or correct any of it before rendering",
    ]
    for phrase in forbidden:
        assert phrase.lower() not in _AGENT_GREETING_HINT.lower(), (
            f"stale metadata-review-checkpoint phrase: {phrase!r}"
        )
```

Update any existing `tests/test_agent_greeting.py` assertions that referenced "keep_image=true", the confirm dance, or the old metadata checkpoint — delete those.

- [ ] **Step 2: Run to see failures**

```
pytest tests/test_agent_greeting.py -v
```

- [ ] **Step 3: Rewrite the constant**

Full replacement for `_AGENT_GREETING_HINT` in `src/repl.py`:

```python
_AGENT_GREETING_HINT = (
    "The user just gave you a PDF draft. Call read_draft to see "
    "what's in it, greet them in the same language they will use "
    "(they haven't spoken yet — default to English but switch once "
    "you see their reply; keep slash commands like /model /render "
    "/load literal — they are REPL tokens, do NOT translate them). "
    "\n\n"
    "PROCESS THE DRAFT AUTOMATICALLY. Do NOT ask the user per-page "
    "confirmations — those days are over. Run the ingestion pipeline "
    "end-to-end without stopping to confirm each mutation:\n"
    "  - For every image-only page, call transcribe_page (no "
    "``keep_image`` argument — the tool classifies the image itself "
    "via <BLANK>/<TEXT>/<MIXED> sentinels).\n"
    "  - Apply obvious typo/OCR-misread fixes via propose_typo_fix "
    "on your own judgement; the tool is bounded (3 words / 30 "
    "chars) so you can't rewrite a sentence.\n"
    "  - Pick per-page layouts via propose_layouts (batch) using "
    "the select-page-layout skill's rules.\n"
    "  - Do NOT ask the user to approve any of this — the tools no "
    "longer take a confirm callback; they auto-apply.\n\n"
    "ASK ONLY FOR THINGS YOU CANNOT INFER. Before the first render, "
    "collect from the user only:\n"
    "  - title (and author) — required, the child is the source of "
    "truth;\n"
    "  - cover choice — offer the three options explicitly: (a) "
    "reuse a page drawing (consult the select-cover-template skill), "
    "(b) generate an AI cover illustration via "
    "generate_cover_illustration (OpenAI-only; a tiny cost confirm "
    "stays on this tool — that's the only surviving gate and it is "
    "about money, not content), or (c) poster style via set_cover "
    "with style='poster';\n"
    "  - back-cover blurb — one short line, verbatim from the user; "
    "'skip' is allowed.\n"
    "Ask each of these as its own one-line question — do not "
    "bundle them into a list the user has to read and parse.\n\n"
    "RENDER IMMEDIATELY after the above. Call render_book; the PDF "
    "opens in the user's viewer automatically.\n\n"
    "POST-RENDER REVIEW TURN. Post exactly one prompt to the user "
    "after a successful render:\n"
    "  'PDF ready. Which page numbers have issues? "
    "(e.g. 3, 5 — or type none / yok / ok / ship to finish.)'\n"
    "Parse the user's reply. If they list page numbers with "
    "corrections in free-form text, dispatch one or more tool calls:\n"
    "  - 'page N text: <verbatim>' → apply_text_correction(N, <verbatim>). "
    "The user's string is the source of truth; do NOT paraphrase, "
    "translate, or fix anything in it.\n"
    "  - 'page N restore' (or equivalent) → restore_page(N).\n"
    "  - 'page N hide' → hide_page(N).\n"
    "  - Cross-page asks ('regenerate the cover, less purple') → "
    "call the appropriate tool; user confirms cost if any.\n"
    "After applying all corrections, call render_book AGAIN and "
    "ask the same review prompt. Loop until the user replies with "
    "an exit token (``none``, ``yok``, ``ok``, ``ship``, ``done``, "
    "``tamam`` — case-insensitive). On exit, close with a single "
    "line pointing at the stable PDF path.\n\n"
    "PRESERVE-CHILD-VOICE. Even though tools don't confirm, the "
    "child's words remain sacred: the OCR prompt still says "
    "'verbatim, do not fix, do not polish'; apply_text_correction "
    "writes the user's string as-is with no model in between; "
    "input files under .book-gen/input/ and the per-page drawings "
    "under .book-gen/images/page-NN.png are NEVER deleted or "
    "rewritten by any tool."
)
```

- [ ] **Step 4: Run**

```
pytest tests/test_agent_greeting.py tests/test_repl*.py -v
```

Expected: pass. Fix any residual stale assertions in `tests/test_repl*.py`.

- [ ] **Step 5: Commit**

```
git add src/repl.py tests/test_agent_greeting.py tests/test_repl*.py
git commit -m "refactor(repl): rewrite greeting around auto-ingest + post-render review turn"
```

---

## Chunk 5: Integration, docs, PR

End-to-end regression, contract docs, user-facing docs, PLAN trim, PR.

### Task 12: End-to-end review-loop integration test

**Files:**
- Create: `tests/test_review_loop.py`
- (Uses existing fake-LLM helpers if present; otherwise inline.)

- [ ] **Step 1: Draft the scripted integration**

Create `tests/test_review_loop.py`:

```python
"""End-to-end: load a draft, auto-ingest, render, respond to the
review prompt with a verbatim correction, re-render, ship.

Exercises the full new flow without mocking the agent's tool loop.
"""

import io
from pathlib import Path
from pypdf import PdfReader
from rich.console import Console

from src.providers.llm import find
from src.repl import Repl


def _scripted(lines):
    it = iter(lines)

    def read():
        try:
            return next(it)
        except StopIteration as e:
            raise EOFError from e

    return read


class _ScriptedLLM:
    """Replies by popping the next canned response each call.

    Supports chat() and turn() — real providers are richer but for the
    review-loop test we only need deterministic tool-call sequences
    and verbatim string returns.
    """

    def __init__(self, tool_call_script, chat_script):
        self._tool_calls = iter(tool_call_script)
        self._chats = iter(chat_script)
        self.name = "scripted"

    def chat(self, *_a, **_kw):
        return next(self._chats)

    # ... turn() shape matches src.providers.llm.LLMProvider.turn
```

Then write the minimal round-trip test. If the existing test suite already has an equivalent scripted-LLM harness, prefer reusing it. Search:

```
rg "class.*Scripted.*LLM" tests/
rg "turn\\s*=" tests/
```

The test asserts:
1. After auto-ingest, the rendered PDF contains page-1 text equal to the OCR output.
2. After the review-turn message `"page 1 text: ÖZEL"`, the re-rendered PDF contains `ÖZEL`.
3. A final `"none"` reply exits the loop cleanly.

- [ ] **Step 2-4: TDD against the scripted harness.**

- [ ] **Step 5: Commit**

```
git add tests/test_review_loop.py
git commit -m "test(repl): end-to-end review-loop integration (render → correction → re-render)"
```

### Task 13: `CLAUDE.md` Core principle + architecture bullets

**Files:**
- Modify: `CLAUDE.md` — "Core principle: the child is the author" section; `src/agent_tools.py` architecture bullet; `src/draft.py` architecture bullet; `src/prune.py` architecture bullet.

- [ ] **Step 1: Rewrite the "Core principle" section**

Replace the current passage with:

```markdown
## Core principle: the child is the author

This project exists so a **child feels like a real author**. Two invariants hold end-to-end:

1. **The input is immutable.** The child's scanned PDF mirror at `.book-gen/input/` and the per-page drawings `pdf_ingest` extracts to `.book-gen/images/page-NN.*` are **never** deleted, rewritten, or renamed by any tool. Anything on the output side can be regenerated from them.

2. **The child's words reach the printed page verbatim.** Every write path goes through a verbatim-preserving prompt (OCR asks for a byte-for-byte transcription, no polishing) or a tool that copies a user-provided string without model processing (`apply_text_correction`). Claude's default instinct to "improve" prose is actively suppressed at prompt level.

Per-mutation y/n confirm gates are NOT how this is enforced — those lived through the first year of the project and became the UX problem they were meant to prevent. The single remaining gate is a **cost** confirm on the two AI illustration tools (`generate_cover_illustration`, `generate_page_illustration`) because they spend money. Everything else auto-applies. The user audits the finished PDF in a post-render review turn; any mistake is reversible via `apply_text_correction` or `restore_page`.
```

- [ ] **Step 2: Update the `src/agent_tools.py` architecture bullet**

The bullet should now list the full tool set: `read_draft`, `propose_typo_fix`, `set_metadata`, `set_cover`, `choose_layout`, `propose_layouts`, `render_book`, `transcribe_page` (with the three-sentinel classification), `hide_page`, `generate_cover_illustration`, `generate_page_illustration`, `apply_text_correction`, `restore_page`. Name the two cost-gated tools explicitly; note `apply_text_correction` and `restore_page` as review-turn-only.

- [ ] **Step 3: Update the `src/draft.py` bullet**

Add a mention of the `DraftPage.hidden` flag and that `to_book` filters it out.

- [ ] **Step 4: Update the `src/prune.py` bullet**

Add the input-preserved guarantee explicitly: `.book-gen/input/*` and `.book-gen/images/page-NN.*` are out of scope for prune *by contract*, not just by current regex coincidence.

- [ ] **Step 5: Commit**

```
git add CLAUDE.md
git commit -m "docs(claude): rewrite Core principle for input-immutable + output-reproducible contract"
```

### Task 14: Rewrite `preserve-child-voice` skill

**Files:**
- Modify: `.claude/skills/preserve-child-voice/SKILL.md`

- [ ] **Step 1: Rewrite the contract**

The skill today framed preservation as per-tool allowed/forbidden edit lists. Replace with the two-invariant contract from CLAUDE.md + concrete rules:

1. The vision/OCR prompt **must** include "verbatim, do not fix, do not polish, do not translate".
2. Any tool that mutates `page.text` without going through an LLM prompt (today only `apply_text_correction`) must pass the incoming string through **unchanged** — no strip, no normalise, no "smart quote" fixing.
3. No code path writes to `.book-gen/input/*` or `.book-gen/images/page-NN.*` after `pdf_ingest` finishes.
4. If the agent proposes a typo fix (`propose_typo_fix`), the bound stays 3 words / 30 chars per side so it can't funnel a sentence rewrite — even auto-applied.

"Allowed/forbidden edits" tables stay in the skill for clarity but are reframed as "things the OCR prompt / tool path guarantees", not "things the agent must ask the user about".

- [ ] **Step 2: Commit**

```
git add .claude/skills/preserve-child-voice/SKILL.md
git commit -m "docs(skill): preserve-child-voice rewritten for input-immutable/verbatim-prompt contract"
```

### Task 15: `README.md` + `docs/PLAN.md` sweep; full suite; PR

- [ ] **Step 1: `README.md` updates**

In the "Status" section:
- Replace the bullet that described the per-mutation confirm flow with one describing the auto-ingest + post-render review loop.
- Add a line naming `hide_page` / `apply_text_correction` / `restore_page` as the review-turn tools, and noting the input-immutable guarantee on `.book-gen/input/` + `.book-gen/images/page-NN.*`.

No new slash command — no change to the `/`-command table.

- [ ] **Step 2: `docs/PLAN.md` updates**

- Move the "Rework preserve-child-voice from pre-approval to post-render review" entry from "Next up" into "Shipped" (with the real PR number once assigned).
- Leave the pagination-blank follow-up item in "Next up" as-is.

- [ ] **Step 3: Full suite**

```
pytest
```

Expected: all pass. Fix any lingering fallout (stale fixture assertions, greetings asserted by other tests).

- [ ] **Step 4: Commit and push**

```
git add README.md docs/PLAN.md
git commit -m "docs: README + PLAN updates for the review-based gate refactor"
git push -u origin refactor/review-based-gate
```

- [ ] **Step 5: Open PR**

Title: `refactor(gate): move preserve-child-voice from pre-approval to post-render review`

Body skeleton:

```
## Summary
- Removes the per-mutation y/n confirm gate on the content tools.
- Adds post-render review turn; new tools `apply_text_correction`,
  `restore_page`, and renamed `hide_page` support it.
- Three-sentinel classification in `transcribe_page` replaces the
  `keep_image` flag.
- Cost confirm stays on the two AI illustration tools.

## Why
<link to spec> + Yavru Dinozor test session showed the per-step
confirms had become the UX problem. Contract moves from
"block-before-write" to "input-immutable / output-reproducible".

## Test plan
- [x] pytest — N passing (up from 614 pre-refactor)
- [x] End-to-end `tests/test_review_loop.py` round-trip.
- [ ] Live test against Yavru Dinozor draft — verify flow end-to-end.

Spec: docs/superpowers/specs/2026-04-22-review-based-gate-design.md
```

---

## What stays as today's behaviour (sanity anchors)

- `render_book` pipeline, A5 PDF shape, A4 booklet imposition.
- `pdf_ingest.extract_images` / `extract_pages` — unchanged.
- `prune.py` regex + module — unchanged; docstring grows a paragraph.
- Slash commands — unchanged.
- Provider selection, keyring, `/model` / `/logout` / `/prune` — unchanged.

## Out of scope (do not expand this plan)

- Real-book pagination blanks in the A4 booklet (separate follow-up in PLAN.md).
- OCR-typo auto-correction beyond `propose_typo_fix`'s existing bounds.
- `/undo` history for the review loop.
- NullProvider (offline) review turn — offline stays with slash commands.

---

## Execution handoff

Ready to execute. This harness has subagents; use `superpowers:subagent-driven-development` for the task-by-task execution.
