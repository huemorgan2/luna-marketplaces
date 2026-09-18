"""The column-type table — one place that drives validation, filter operators,
and what the UI editor/renderer should do.

Every type sits on a primitive base (text / number / bool). Semantic types
(email, phone, address, currency, percent) add validation or render config on
top — nothing exotic. `coerce()` normalizes an incoming cell value or raises
CellInvalid with a message the agent/UI can show on the cell.
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

from .models import MAX_CELL_CHARS


class CellInvalid(ValueError):
    """A cell value that does not fit its column type."""


# monday-style palette the UI offers for status options
STATUS_COLORS = (
    "#00c875",  # green
    "#e2445c",  # red
    "#fdab3d",  # orange
    "#579bfc",  # blue
    "#a25ddc",  # purple
    "#ffcb00",  # yellow
    "#0086c0",  # dark blue
    "#9aadbd",  # gray
)

DEFAULT_STATUS_OPTIONS = [
    {"id": "todo", "label": "Todo", "color": "#9aadbd"},
    {"id": "working", "label": "Working on it", "color": "#fdab3d"},
    {"id": "done", "label": "Done", "color": "#00c875"},
    {"id": "stuck", "label": "Stuck", "color": "#e2445c"},
]

_TEXT_OPS = ("eq", "neq", "contains", "not_contains", "is_empty", "not_empty")
_NUMBER_OPS = ("eq", "neq", "gt", "gte", "lt", "lte", "is_empty", "not_empty")
_STATUS_OPS = ("is", "is_not", "is_any_of", "is_empty", "not_empty")
_BOOL_OPS = ("is",)

# type -> (base primitive, filter ops)
COLUMN_TYPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "text": ("text", _TEXT_OPS),
    "number": ("number", _NUMBER_OPS),
    "status": ("status", _STATUS_OPS),
    "date": ("date", _NUMBER_OPS),  # dates compare lexicographically as ISO
    "checkbox": ("bool", _BOOL_OPS),
    "email": ("text", _TEXT_OPS),
    "phone": ("text", _TEXT_OPS),
    "address": ("text", _TEXT_OPS),
    "currency": ("number", _NUMBER_OPS),
    "percent": ("number", _NUMBER_OPS),
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def base_of(col_type: str) -> str:
    if col_type not in COLUMN_TYPES:
        raise CellInvalid(f"unknown column type '{col_type}'")
    return COLUMN_TYPES[col_type][0]


def ops_for(col_type: str) -> tuple[str, ...]:
    return COLUMN_TYPES[col_type][1]


def _as_text(value: Any, what: str) -> str:
    if not isinstance(value, str):
        value = str(value)
    if len(value) > MAX_CELL_CHARS:
        raise CellInvalid(f"{what} longer than {MAX_CELL_CHARS} chars")
    return value


def _as_number(value: Any, what: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            raise CellInvalid(f"{what} must be a number, got {value!r}") from None
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CellInvalid(f"{what} must be finite")
        if value.is_integer():
            value = int(value)
    return value


def coerce(col_type: str, value: Any, options: dict | None = None) -> Any:
    """Normalize `value` for storage in a cell of `col_type`, or raise
    CellInvalid. None / "" always mean 'empty cell' and pass through as None."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    options = options or {}
    base = base_of(col_type)

    if col_type == "email":
        text = _as_text(value, "email").strip()
        if not _EMAIL_RE.match(text):
            raise CellInvalid(f"'{text}' is not an email address")
        return text
    if col_type == "date":
        text = _as_text(value, "date").strip()
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            raise CellInvalid(f"'{text}' is not an ISO date (YYYY-MM-DD)") from None
    if col_type == "status":
        text = _as_text(value, "status").strip()
        choices = options.get("choices") or []
        for c in choices:
            if text == c.get("id") or text.lower() == str(c.get("label", "")).lower():
                return c["id"]
        labels = [c.get("label") for c in choices]
        raise CellInvalid(f"'{text}' is not a status of this column; choices: {labels}")
    if base == "text":
        return _as_text(value, col_type)
    if base == "number":
        return _as_number(value, col_type)
    if base == "bool":
        if isinstance(value, bool):
            return value
        if str(value).strip().lower() in ("true", "1", "yes", "checked"):
            return True
        if str(value).strip().lower() in ("false", "0", "no", "unchecked"):
            return False
        raise CellInvalid(f"checkbox must be true/false, got {value!r}")
    raise CellInvalid(f"unknown column type '{col_type}'")  # pragma: no cover


def default_options(col_type: str, options: dict | None = None) -> dict:
    """Fill per-type option defaults (status choices get ids + colors)."""
    options = dict(options or {})
    if col_type == "status":
        raw = options.get("choices")
        if not raw:
            options["choices"] = [dict(c) for c in DEFAULT_STATUS_OPTIONS]
        else:
            choices = []
            for i, c in enumerate(raw):
                if isinstance(c, str):
                    c = {"label": c}
                cid = c.get("id") or _slug(c.get("label", f"opt-{i}"))
                choices.append(
                    {
                        "id": cid,
                        "label": c.get("label", cid),
                        "color": c.get("color") or STATUS_COLORS[i % len(STATUS_COLORS)],
                    }
                )
            options["choices"] = choices
    if col_type == "currency":
        options.setdefault("symbol", "$")
        options.setdefault("position", "prefix")  # prefix | suffix
    if col_type == "percent":
        options.setdefault("decimals", 0)
    return options


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return s or "opt"
