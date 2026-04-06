# DAIO — Four-Tier Project Roadmap

## Locked Intake Variables

| # | Variable | Value |
|---|----------|-------|
| 1 | Target Codebase | Runtime config — tool is target-agnostic |
| 2 | Refactoring Goal | Runtime config via `rules.md` input |
| 3 | Rules File | Scaffold template as part of tool |
| 4 | Local LLM | Any Ollama-compatible model, selected via `config.yaml` |
| 5 | Test Coverage | Runtime config — tool detects and uses existing pytest |
| 6 | Priority Order | **Reverse line order** (bottom-up within file) |
| 7 | Scope | Runtime config — full codebase, module, or file list |

| Supp | Variable | Value |
|------|----------|-------|
| A | Hardware | Intel CPU ~20GB RAM **or** M2 Pro 64GB RAM. CPU inference. |
| B | Pipeline Scope | Reusable tool — target-agnostic orchestrator |
| C | Concurrency | Sequential (one function at a time) |
| D | Rollback | Per-function `git commit`, togglable via config |

---

## Tier 1 — Phase Overview Table

| Phase | Name | Description | Primary Artifact | Type |
|-------|------|-------------|-----------------|------|
| 1 | **Cartographer** | AST-parse target codebase, build manifest with line offsets, dependency weights, UIDs | `manifest.json` | 🔵 DET |
| 2 | **Sieve** | Extract target snippet, prepend pruned global header, enforce token budget | `work_packet.txt` | 🔵 DET |
| 3 | **Surgeon** | Dispatch to LLM → extract response → validate → apply → recalc offsets | Modified source + updated `manifest.json` | 🔵+🟡 MIXED |
| 4 | **Audit Trail** | Run tests on modified blocks, auto-rollback on failure, write audit log | `audit_log.json` | 🔵 DET |

---

## Tier 2 — Milestone Breakdown

### M0: Project Scaffold & Config System

> **Effort: M (2–8 hours)** | 🔵 DET

Build the project skeleton, config schema, and CLI entry point.

**Tasks:**

1. 🔵 **Project structure** — `pyproject.toml`, package layout (`daio/`), dependencies (S)
2. 🔵 **Config schema** — `config.yaml` with all runtime knobs (M)
   - `model`: Ollama model name (string)
   - `token_budget`: max tokens for work packet (int, default 4096)
   - `max_retries`: LLM retry limit (int, default 3)
   - `auto_commit`: per-function git commit toggle (bool, default true)
   - `target_path`: path to target codebase (string)
   - `rules_path`: path to rules.md (string)
   - `scope`: `"full"` | `"module"` | `"filelist"` + optional file list
   - `ruff_config`: optional path to ruff config
3. 🔵 **CLI entry point** — `daio run`, `daio validate-config`, `daio show-manifest` (S)
4. 🔵 **`rules.md` template** — scaffolded example with common refactoring instructions (S)

**Acceptance Criteria:**
- `daio validate-config` parses a config file and reports errors for missing/invalid fields
- `pip install -e .` installs the tool
- Config loads with sane defaults for all optional fields

**Failure Mode:**
- Config schema drift — fields added later break early consumers → **Mitigation:** Use `pydantic` for schema validation with strict mode; schema is the single source of truth

---

### M1: Cartographer — AST Manifest Builder

> **Effort: M (2–8 hours)** | 🔵 DET

Parse target Python files via `ast` module. Build `manifest.json` with every function/class, their line ranges, UIDs, and dependency weights.

**Tasks:**

1. 🔵 **AST walker** — Visit `FunctionDef`, `AsyncFunctionDef`, `ClassDef` nodes (M)
   - Extract: name, start_line, end_line, decorator list, docstring presence
   - Compute body LOC (end_line - start_line)
2. 🔵 **UID generation** — `SHA256(filename + ":" + start_line)` truncated to 12 hex chars (S)
   - Collision check: fail-fast if any duplicate UIDs within manifest
3. 🔵 **Dependency weight** — Count references to each function name across all in-scope files via `ast.Name` node visitor (M)
4. 🔵 **UID anchor injection** — Insert `# UID:<hash>:START` / `# UID:<hash>:END` comment markers around each function in the source file (M)
   - Inject bottom-up (reverse line order) to avoid offset drift during injection
5. 🔵 **Manifest serialization** — Write `manifest.json` with schema:

```json
{
  "version": 1,
  "generated_at": "<ISO8601>",
  "files": {
    "path/to/file.py": {
      "functions": [
        {
          "name": "func_name",
          "uid": "a1b2c3d4e5f6",
          "start_line": 42,
          "end_line": 67,
          "body_loc": 25,
          "dependency_weight": 7,
          "has_docstring": false,
          "status": "PENDING",
          "dirty": true
        }
      ]
    }
  }
}
```

6. 🔵 **Reverse-line-order sort** — Sort functions within each file by `start_line` descending for processing (S)

**Acceptance Criteria:**
- Running Cartographer on a test file with 5 functions produces correct `manifest.json`
- UID anchors are correctly injected without corrupting syntax (`py_compile` passes)
- No duplicate UIDs across entire manifest
- Functions sorted bottom-up within each file entry

**Failure Modes:**
- **Anchor Collision** → SHA256(filename:line) virtually eliminates this; validated with uniqueness assertion
- **Syntax corruption from anchor injection** → `py_compile` gate after injection; rollback on failure
- **Nested functions / closures** → Decision: process only top-level functions initially (inner functions travel with their parent). Flag nested functions in manifest as `"nested": true, "status": "SKIPPED"`

---

### M2: Sieve — Context Pruner & Work Packet Assembler

> **Effort: M (2–8 hours)** | 🔵 DET

Extract the target function snippet, build a minimal global header, and assemble a token-budgeted work packet.

**Tasks:**

1. 🔵 **Snippet extractor** — Read source lines between UID anchors (S)
2. 🔵 **Import collector** — Parse file-level imports via AST, filter to only those referenced in snippet (M)
   - Use `ast.Name` / `ast.Attribute` nodes in snippet to build reference set
   - `grep`-based fallback for dynamic imports or string references
3. 🔵 **Global header builder** — Assemble: filtered imports + referenced constants/type aliases (M)
   - Cap at configurable `header_token_budget` (default: 512 tokens)
   - Truncate with `# [TRUNCATED — N identifiers omitted]` comment if over budget
4. 🔵 **Work packet assembler** — Stitch together (M):
   ```
   === RULES ===
   <contents of rules.md>
   === GLOBAL CONTEXT ===
   <filtered imports + constants>
   === TARGET FUNCTION ===
   # UID:<hash>:START
   <function code>
   # UID:<hash>:END
   === INSTRUCTION ===
   Transform the function between UID markers according to the RULES above.
   Return ONLY the transformed function, preserving UID markers exactly.
   ```
5. 🔵 **Token counter** — Estimate token count (chars/4 heuristic or `tiktoken` if available) (S)
   - WARN if packet exceeds `token_budget` from config
   - ABORT if packet exceeds 2x `token_budget` (function too large for model)

**Acceptance Criteria:**
- Work packet for a 30-line function with 5 imports includes only the 2 imports actually referenced
- Token count is within budget for standard functions
- Oversized functions are flagged and skipped with `status: "SKIPPED_OVERSIZED"` in manifest

**Failure Modes:**
- **Context Bleed** → Hard cap on header tokens + relevance filter on imports → **Mitigation:** measurable, testable budget
- **Dynamic imports missed** → `grep` fallback catches string-based references like `getattr(module, "func")`
- **Function too large** → Skip and log rather than sending degraded prompt

---

### M3: Surgeon — Refinement Loop

> **Effort: L (1–3 days)** | 🔵+🟡 MIXED

The core execution engine. Dispatch work packet to Ollama, extract response, validate, apply, and recalculate offsets.

**Tasks:**

1. 🟡 **Ollama client** — HTTP client for `POST /api/generate` (M)
   - Configurable model name from `config.yaml`
   - Timeout handling (local CPU inference can be slow — 5-10 min timeout)
   - Streaming response collection
   - **Wrapped in 🔵 DET gatekeeper:** client is a pure function (prompt in → text out)
2. 🔵 **Response extractor** — Compiled regex to capture code between UID markers (S)
   - Pattern: `# UID:<hash>:START\n(.*?)# UID:<hash>:END`
   - Fail if markers not found → trigger retry with "You must preserve UID markers" appended to prompt
3. 🔵 **Validation gate** — Three-stage check (M)
   - Stage 1: `py_compile` on extracted code (syntax)
   - Stage 2: `ruff check` on extracted code (lint)
   - Stage 3: Line-count sanity — reject if output is <30% or >300% of input LOC (hallucination guard)
4. 🟡 **Retry loop** — On validation failure (M)
   - Inject error message into work packet: `"Previous attempt failed: <error>. Fix and retry."`
   - Max retries from config (default 3)
   - On max retries exceeded: mark function `status: "FAILED"` in manifest, skip, continue
5. 🔵 **Apply — Delete-and-Reinsert** — Replace original lines with validated output (M)
   - Read file into memory
   - Delete lines `[start_line, end_line]`
   - Insert new lines at `start_line`
   - Write file atomically (write to `.tmp` → `os.replace()`)
6. 🔵 **Offset recalculation** — Update all downstream entries in manifest (S)
   - `delta = new_end_line - old_end_line`
   - For every function in same file with `start_line > old_start_line`: adjust by delta
   - Since we process bottom-up, this only affects already-processed functions (safe)
7. 🔵 **Per-function git commit** — If `auto_commit: true` in config (S)
   - `git add <file> && git commit -m "daio: refactored <func_name> [<uid>]"`
   - On commit failure: log warning, continue (don't block pipeline)

**Acceptance Criteria:**
- Valid LLM response is correctly extracted, validated, and applied
- Invalid response triggers retry with error context
- After 3 failures, function is skipped and marked `FAILED`
- Offset recalc produces correct line numbers for subsequent functions
- Atomic file write prevents corruption

**Failure Modes:**
- **LLM Hallucination** → 3-stage validation gate + retry loop + skip-on-max
- **Line Offset Drift** → Bottom-up processing + immediate recalc after every apply
- **Ollama timeout/crash** → HTTP timeout → mark as `FAILED`, continue
- **Atomic write failure** → `os.replace()` is atomic on POSIX; `.tmp` file cleaned on failure

---

### M4: Audit Trail — Verification, Rollback, Logging

> **Effort: M (2–8 hours)** | 🔵 DET

Post-pipeline verification: run tests, rollback failures, generate audit log.

**Tasks:**

1. 🔵 **Test runner** — Invoke `pytest` on modified files (M)
   - If target has pytest config: run relevant test files
   - If no tests: skip with `"test_status": "NO_TESTS"` in audit log
   - Capture stdout/stderr and exit code
2. 🔵 **Auto-rollback** — On test failure with `auto_commit: true` (M)
   - `git revert <commit_hash>` for each failing function's commit
   - Update manifest: `status: "REVERTED"`
   - If `auto_commit: false`: log failure, do not rollback (user manages state)
3. 🔵 **Audit log generation** — Write `audit_log.json` (M)

```json
{
  "run_id": "<UUID4>",
  "timestamp": "<ISO8601>",
  "config": { "model": "...", "token_budget": 4096 },
  "summary": {
    "total_functions": 42,
    "succeeded": 38,
    "failed": 2,
    "skipped": 2,
    "reverted": 1
  },
  "details": [
    {
      "uid": "a1b2c3d4e5f6",
      "function": "func_name",
      "file": "path/to/file.py",
      "status": "SUCCESS",
      "retries": 0,
      "validation_errors": [],
      "test_result": "PASS",
      "commit_hash": "abc1234",
      "duration_seconds": 45.2
    }
  ]
}
```

**Acceptance Criteria:**
- Test failures trigger rollback of the specific function's commit (not the entire run)
- Audit log accurately reflects every function's journey through the pipeline
- `daio` exits with code 0 if all functions succeed, code 1 if any failed/reverted

**Failure Modes:**
- **No pytest available** → Graceful degradation: skip tests, log `NO_TESTS`
- **Rollback of merge conflict** → If manual edits happened between commits → `git revert` may fail → Log `ROLLBACK_FAILED`, require manual intervention

---

### M5: Integration, CLI Polish & Smoke Test

> **Effort: L (1–3 days)** | 🔵 DET

Wire all phases together, polish CLI UX, and validate end-to-end on a synthetic target.

**Tasks:**

1. 🔵 **Pipeline orchestrator** — Sequential phase executor: Cartographer → Sieve → Surgeon → Audit Trail (M)
   - Phase-level error boundaries: if Cartographer fails, abort clean
   - Progress reporting to stdout (function N of M, phase name, status)
2. 🔵 **Synthetic test target** — Create a small Python package (~10 functions) with deliberate gaps (S)
   - Missing docstrings, no type hints, inconsistent style
   - Includes edge cases: nested functions, decorators, async defs, empty functions
3. 🔵 **Sample `rules.md`** — "Add Google-style docstrings with Args/Returns/Raises sections" (S)
4. 🔵 **End-to-end smoke test** — Run full pipeline on synthetic target (L)
   - Validate manifest correctness
   - Validate work packets stay within token budget
   - Validate git history has per-function commits
   - Validate audit log completeness
5. 🔵 **CLI polish** (M)
   - `daio run --config config.yaml` — full pipeline
   - `daio init` — generate default `config.yaml` + `rules.md` template
   - `daio manifest --config config.yaml` — run Cartographer only, inspect manifest
   - `daio dry-run --config config.yaml` — generate work packets without dispatching to LLM
   - Rich terminal output (progress bars, colored status)
6. 🔵 **pytest suite for the tool itself** (M)
   - Unit tests for AST walker, UID generator, token counter, offset recalc
   - Integration test for full pipeline on synthetic target (mocked Ollama)

**Acceptance Criteria:**
- `daio run --config config.yaml` completes end-to-end on synthetic target
- All synthetic functions are refactored with valid docstrings
- Git log shows individual per-function commits
- Audit log shows 100% success on synthetic target
- `daio dry-run` generates work packets without touching Ollama

**Failure Modes:**
- **Ollama not running** → Clear error: `"Ollama is not reachable at <url>. Start with: ollama serve"`
- **Target not a git repo** → Fail-fast: `"auto_commit requires a git repository. Run 'git init' or set auto_commit: false"`

---

## Tier 3 — Critical Path (MVP Single-File Pipeline)

The minimum viable pipeline to refactor **one function in one file**:

| Step | Action | Type | Depends On |
|------|--------|------|------------|
| 1 | Parse `config.yaml` + validate | 🔵 DET | — |
| 2 | AST-walk target file → build manifest entry | 🔵 DET | Step 1 |
| 3 | Inject UID anchors into source (bottom-up) | 🔵 DET | Step 2 |
| 4 | Extract snippet + build work packet | 🔵 DET | Step 3 |
| 5 | Dispatch work packet to Ollama | 🟡 PROB | Step 4 |
| 6 | Extract + validate response (py_compile + ruff) | 🔵 DET | Step 5 |
| 7 | Delete-and-reinsert validated code into source | 🔵 DET | Step 6 |
| 8 | Git commit + write audit log entry | 🔵 DET | Step 7 |

> [!TIP]
> **MVP Recommendation:** Implement the Critical Path first (Steps 1–8 for a single function). This validates the entire control flow before scaling to multi-function/multi-file processing. Estimated effort for MVP: **M (4–6 hours)** assuming Ollama is already running.

---

## Tier 4 — Open Questions Log

- **Q1:** What Python version minimum? (3.10+ assumed for `match` statement support in CLI — confirm?)
- **Q2:** `rules.md` format — free-form markdown, or structured YAML with specific fields? (Proposing free-form markdown — LLM reads it as-is)
- **Q3:** Should the tool support non-Python targets in the future? (Current design is Python-specific due to `ast` + `py_compile` + `ruff` chain. Other languages would need a pluggable validator architecture — flag as future scope?)
- **Q4:** Token counting strategy — `chars/4` heuristic vs. `tiktoken` library? (Ollama models use various tokenizers; exact counting requires model-specific tokenizer. Proposing `chars/4` with a configurable safety margin.)
- **Q5:** Class methods — process each method independently, or the entire class as one unit? (Proposing: individual methods, with the class signature + `__init__` included in the global header for context.)
- **Q6 (Assumption):** The first demo use case will be "add Google-style docstrings." If you have a different initial transformation in mind, surface it now — it affects the `rules.md` template and smoke test validation.

---

## Proposed Project Layout

```
DAIO/
├── pyproject.toml
├── README.md
├── config.example.yaml
├── rules.example.md
├── daio/
│   ├── __init__.py
│   ├── cli.py                  # Click-based CLI entry points
│   ├── config.py               # Pydantic config schema + loader
│   ├── cartographer/
│   │   ├── __init__.py
│   │   ├── ast_walker.py       # AST parsing + function extraction
│   │   ├── uid.py              # UID generation + collision check
│   │   ├── anchor.py           # UID anchor injection into source
│   │   └── manifest.py         # Manifest serialization + sorting
│   ├── sieve/
│   │   ├── __init__.py
│   │   ├── snippet.py          # Snippet extraction by UID markers
│   │   ├── header.py           # Import/constant relevance filter
│   │   ├── token_counter.py    # Token budget estimation
│   │   └── work_packet.py      # Work packet assembly
│   ├── surgeon/
│   │   ├── __init__.py
│   │   ├── ollama_client.py    # Ollama HTTP client (generic)
│   │   ├── extractor.py        # Regex extraction from LLM response
│   │   ├── validator.py        # py_compile + ruff + LOC sanity
│   │   ├── applicator.py       # Delete-and-reinsert + atomic write
│   │   └── offset.py           # Downstream offset recalculation
│   ├── audit/
│   │   ├── __init__.py
│   │   ├── git_ops.py          # Per-function commit + rollback
│   │   ├── test_runner.py      # pytest invocation + capture
│   │   └── logger.py           # Audit log generation
│   └── pipeline.py             # Phase orchestrator (sequential)
├── tests/
│   ├── conftest.py
│   ├── test_ast_walker.py
│   ├── test_uid.py
│   ├── test_work_packet.py
│   ├── test_validator.py
│   ├── test_offset.py
│   └── test_pipeline.py        # Integration test (mocked Ollama)
└── fixtures/
    └── synthetic_target/       # Synthetic Python package for smoke testing
        ├── __init__.py
        ├── math_utils.py
        ├── string_helpers.py
        └── data_processor.py
```

## Verification Plan

### Automated Tests
- `pytest tests/` — Unit tests for each module (AST walker, UID, token counter, offset recalc, validator)
- `pytest tests/test_pipeline.py` — Integration test with mocked Ollama responses
- `daio dry-run --config config.example.yaml` — Validates Cartographer + Sieve without LLM

### Manual Verification
1. Start Ollama: `ollama serve`
2. Pull a small model: `ollama pull qwen2.5-coder:1.5b` (for fast testing)
3. Run: `daio run --config config.example.yaml` against the synthetic target
4. Inspect: `git log --oneline` to verify per-function commits
5. Inspect: `audit_log.json` to verify all functions processed
6. Review: at least 2 refactored functions manually to confirm docstring quality
