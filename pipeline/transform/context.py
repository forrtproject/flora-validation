"""Explicit state passed between transform_sources.build() stages."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd


@dataclass
class BuildContext:
    verbose: bool
    review_issue: bool
    say: Callable[..., None]
    aliases: dict[str, str]
    exclusions: list[dict]
    dedup_decisions: list[dict]
    cross_type: list[dict]
    problems: dict[str, list] = field(
        default_factory=lambda: {"unknown_alias": [], "bad_alias": [], "invalid_axis": []}
    )
    conflicts: list[dict] = field(default_factory=list)
    hand_fills: list[Any] = field(default_factory=list)
    publication_identifiers: dict | None = None
    meta: dict = field(default_factory=dict)
    join_fallbacks: pd.DataFrame | None = None
    dedup_log: pd.DataFrame = field(default_factory=pd.DataFrame)
    identifier_edits: dict = field(default_factory=dict)

    def normalise_outcome(self, value):
        text = (
            ""
            if value is None or (isinstance(value, float) and pd.isna(value))
            else str(value).strip()
        )
        return self.aliases.get(text, text) or None
