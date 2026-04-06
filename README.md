# DAIO — Deterministic AI Orchestration

A compiler-verified agentic refactoring pipeline that uses local LLMs as stateless workers
to transform Python codebases with zero context drift.

## Quick Start

```bash
pip install -e ".[dev]"
daio init                          # Generate config.yaml + rules.md
daio run --config config.yaml      # Run full pipeline
daio dry-run --config config.yaml  # Generate work packets without LLM
daio manifest --config config.yaml # Inspect AST manifest only
```

## Architecture

The **Core Invariant**: The Orchestrator (Python kernel) owns ALL filesystem authority.
The Worker (local LLM) is a stateless pure function — snippet in, snippet out.

| Phase | Name | Type |
|-------|------|------|
| 1 | Cartographer | 🔵 DET — AST parsing → manifest.json |
| 2 | Sieve | 🔵 DET — Context pruning → work_packet.txt |
| 3 | Surgeon | 🔵+🟡 MIXED — LLM dispatch + validation loop |
| 4 | Audit Trail | 🔵 DET — Test, rollback, audit log |

## Configuration

All runtime behavior is controlled via `config.yaml`. See `config.example.yaml` for all options.

## License

MIT
