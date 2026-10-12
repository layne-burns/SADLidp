"""The adapter contract (SPEC.md §5.1)."""

from __future__ import annotations

from dataclasses import dataclass, field

OK = "ok"
STATUSES = {OK, "skipped", "not_yet_published", "suspect", "blocked", "parse_error", "budget", "failed"}
SUSPECT = "suspect"  # the response looks incomplete (e.g. the source is mid-update): not merged


@dataclass
class AdapterResult:
    name: str
    kind: str  # "projection" or "rank"
    status: str
    week: int | None = None  # the week the source claims
    fetched_at: str | None = None  # ISO timestamp
    rows: dict[str, dict] = field(default_factory=dict)  # sleeper_id -> {"stats": {...}} or {"rank": r}
    no_projection_ids: set[str] = field(default_factory=set)  # placeholders: missing, never 0
    universe: dict[str, dict] = field(default_factory=dict)  # slot -> {"complete": bool, "depth": int|None}
    covered_keys: set[str] = field(default_factory=set)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == OK

    def summary(self) -> dict:
        return {
            "status": self.status,
            "kind": self.kind,
            "week": self.week,
            "fetched_at": self.fetched_at,
            "rows": len(self.rows),
            "no_projection": len(self.no_projection_ids),
            "error": self.error,
        }
