"""Structured audit logger — per-function event recording.

Writes a JSONL (JSON Lines) audit log where each line is a self-contained
event record. This format is append-friendly, grep-friendly, and trivially
parseable — ideal for post-run forensics.

Event types:
    - DISPATCH: LLM call started
    - EXTRACT: Code extracted from response
    - VALIDATE: Validation gate result
    - APPLY: Code applied to source file
    - COMMIT: Git commit recorded
    - SKIP: Function skipped (oversized, nested, etc.)
    - FAIL: Function failed after all retries
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLogger:
    """Append-only JSONL audit logger.

    Each log entry is a single JSON line with:
        - timestamp (ISO 8601)
        - event_type
        - uid
        - function_name
        - file_path
        - data (event-specific payload)

    Args:
        log_path: Path to the audit.jsonl file.

    Examples:
        >>> logger = AuditLogger(Path(".daio/audit.jsonl"))
        >>> logger.log("DISPATCH", "abc123def456", "my_func", "src/main.py", {"model": "qwen"})
    """

    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._start_time = time.monotonic()

    @property
    def log_path(self) -> Path:
        """Path to the audit log file."""
        return self._log_path

    def log(
        self,
        event_type: str,
        uid: str,
        function_name: str,
        file_path: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Append a single event record to the audit log.

        Args:
            event_type: One of DISPATCH, EXTRACT, VALIDATE, APPLY, COMMIT, SKIP, FAIL.
            uid: Function UID (12 hex chars).
            function_name: Name of the function.
            file_path: Relative path to the source file.
            data: Optional event-specific payload dict.
        """
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.monotonic() - self._start_time, 3),
            "event": event_type,
            "uid": uid,
            "function": function_name,
            "file": file_path,
            "data": data or {},
        }

        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_pipeline_start(self, config_summary: dict[str, Any]) -> None:
        """Log the pipeline start event with configuration summary.

        Args:
            config_summary: Dict of key config values for the audit record.
        """
        self.log("PIPELINE_START", "", "", "", config_summary)

    def log_pipeline_end(self, summary: dict[str, Any]) -> None:
        """Log the pipeline completion event with final summary.

        Args:
            summary: Dict with counts (succeeded, failed, skipped, duration).
        """
        self.log("PIPELINE_END", "", "", "", summary)


def load_audit_log(log_path: Path) -> list[dict[str, Any]]:
    """Read and parse a JSONL audit log.

    Args:
        log_path: Path to the audit.jsonl file.

    Returns:
        List of parsed event dicts.

    Raises:
        FileNotFoundError: If the log file doesn't exist.
    """
    if not log_path.exists():
        msg = f"Audit log not found: {log_path}"
        raise FileNotFoundError(msg)

    events: list[dict[str, Any]] = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
