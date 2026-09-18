"""DbStore — all reads/writes for plugin-db, the single seam shared by the
agent tools and the REST routes (so the search box and db_query can never
disagree).

Querying: rows are fetched per-table (hard-capped at 10k) and q/filters/sort
run in Python — correct and dialect-blind on Postgres and SQLite alike, and
milliseconds at this scale. The no-filter browse path paginates in SQL.
"""

from __future__ import annotations

import re
import uuid as _uuid
from typing import Any, Awaitable, Callable

from sqlalchemy import delete, func, literal, select
from sqlalchemy.exc import IntegrityError

from luna_sdk import JSONB

from .models import (
    MAX_BULK_ROWS,
    MAX_COLUMNS_PER_TABLE,
    MAX_ROWS_PER_TABLE,
    DbColumn,
    DbRow,
    DbTable,
)
from .types import CellInvalid, base_of, coerce, default_options, ops_for

__all__ = [
    "DbStore",
    "TableNotFound",
    "ColumnNotFound",
    "RowNotFound",
    "CellInvalid",
]


class TableNotFound(LookupError):
    pass


class ColumnNotFound(LookupError):
    pass


class RowNotFound(LookupError):
    pass


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:64] or "col"


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _col_dict(c: DbColumn) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "key": c.key,
        "label": c.label,
        "type": c.type,
        "options": c.options or {},
        "width": c.width,
        "position": c.position,
        "required": c.required,
    }


def _row_dict(r: DbRow) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "data": r.data or {},
        "position": r.position,
        "created_at": _iso(r.created_at),
        "updated_at": _iso(r.updated_at),
    }


class DbStore:
    def __init__(self, session_factory) -> None:
        self._sf = session_factory
        # set by the plugin: called after every mutation with a small event
        self.on_change: Callable[[dict], Awaitable[None]] | None = None

    async def _emit(self, action: str, table: DbTable, **extra: Any) -> None:
        if self.on_change is not None:
            await self.on_change(
                {"action": action, "table_id": str(table.id), "table": table.name, **extra}
            )

    # ---- resolution ----------------------------------------------------

    async def _table(self, s, ref: str) -> DbTable:
        """Resolve a luna-table by name (case-insensitive) or id."""
        ref = (ref or "").strip()
        try:
            t = await s.get(DbTable, _uuid.UUID(ref))
            if t is not None:
                return t
        except (ValueError, AttributeError):
            pass
        t = (
            await s.execute(select(DbTable).where(func.lower(DbTable.name) == ref.lower()))
        ).scalar_one_or_none()
        if t is None:
            raise TableNotFound(ref)
        return t

    async def _columns(self, s, table_id) -> list[DbColumn]:
        return list(
            (
                await s.execute(
                    select(DbColumn)
                    .where(DbColumn.table_id == table_id)
                    .order_by(DbColumn.position, DbColumn.created_at)
                )
            ).scalars()
        )

    async def _column(self, s, table: DbTable, ref: str) -> DbColumn:
        """Resolve a column by key, label (case-insensitive), or id."""
        ref = (ref or "").strip()
        cols = await self._columns(s, table.id)
        for c in cols:
            if ref == c.key or ref == str(c.id) or ref.lower() == c.label.lower():
                return c
        raise ColumnNotFound(ref)

    async def _row_count(self, s, table_id) -> int:
        return (
            await s.execute(select(func.count(DbRow.id)).where(DbRow.table_id == table_id))
        ).scalar_one()

    # ---- tables --------------------------------------------------------

    async def list_tables(self) -> list[dict[str, Any]]:
        async with self._sf() as s:
            tables = list(
                (await s.execute(select(DbTable).order_by(DbTable.position, DbTable.created_at)))
                .scalars()
            )
            counts = dict(
                (
                    await s.execute(
                        select(DbRow.table_id, func.count(DbRow.id)).group_by(DbRow.table_id)
                    )
                ).all()
            )
            return [
                {
                    "id": str(t.id),
                    "name": t.name,
                    "description": t.description,
                    "position": t.position,
                    "row_count": counts.get(t.id, 0),
                    "updated_at": _iso(t.updated_at),
                }
                for t in tables
            ]

    async def create_table(
        self,
        name: str,
        description: str = "",
        columns: list[dict] | None = None,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("table name is required")
        if columns is not None and len(columns) > MAX_COLUMNS_PER_TABLE:
            raise ValueError(f"at most {MAX_COLUMNS_PER_TABLE} columns per table")
        async with self._sf() as s:
            exists = (
                await s.execute(select(DbTable).where(func.lower(DbTable.name) == name.lower()))
            ).scalar_one_or_none()
            if exists is not None:
                raise ValueError(f"a table named '{name}' already exists")
            max_pos = (await s.execute(select(func.max(DbTable.position)))).scalar_one() or 0
            table = DbTable(name=name, description=description or "", position=max_pos + 1)
            s.add(table)
            await s.flush()
            # monday-like default schema when none given: a text column + status
            specs = columns if columns else [
                {"label": "Name", "type": "text"},
                {"label": "Status", "type": "status"},
            ]
            keys: set[str] = set()
            for i, spec in enumerate(specs):
                col = self._build_column(table.id, spec, position=i, taken=keys)
                keys.add(col.key)
                s.add(col)
            await s.commit()
            out = await self.get_table(str(table.id))
        await self._emit("table.created", table)
        return out

    def _build_column(self, table_id, spec: dict, position: int, taken: set[str]) -> DbColumn:
        label = str(spec.get("label") or spec.get("name") or "").strip()
        if not label:
            raise ValueError("every column needs a label")
        col_type = str(spec.get("type") or "text").strip().lower()
        base_of(col_type)  # raises CellInvalid on unknown type
        key = spec.get("key") or _slugify(label)
        base_key, n = key, 2
        while key in taken:
            key, n = f"{base_key}-{n}", n + 1
        return DbColumn(
            table_id=table_id,
            key=key,
            label=label,
            type=col_type,
            options=default_options(col_type, spec.get("options")),
            position=position,
            required=bool(spec.get("required", False)),
        )

    async def get_table(self, ref: str) -> dict[str, Any]:
        async with self._sf() as s:
            t = await self._table(s, ref)
            cols = await self._columns(s, t.id)
            count = await self._row_count(s, t.id)
            return {
                "id": str(t.id),
                "name": t.name,
                "description": t.description,
                "position": t.position,
                "columns": [_col_dict(c) for c in cols],
                "row_count": count,
                "max_rows": MAX_ROWS_PER_TABLE,
                "free_slots": MAX_ROWS_PER_TABLE - count,
                "filter_ops": {c.key: list(ops_for(c.type)) for c in cols},
                "updated_at": _iso(t.updated_at),
            }

    async def rename_table(
        self, ref: str, name: str | None = None, description: str | None = None
    ) -> dict[str, Any]:
        async with self._sf() as s:
            t = await self._table(s, ref)
            if name is not None and name.strip():
                clash = (
                    await s.execute(
                        select(DbTable).where(
                            func.lower(DbTable.name) == name.strip().lower(),
                            DbTable.id != t.id,
                        )
                    )
                ).scalar_one_or_none()
                if clash is not None:
                    raise ValueError(f"a table named '{name.strip()}' already exists")
                t.name = name.strip()
            if description is not None:
                t.description = description
            await s.commit()
        await self._emit("table.updated", t)
        return await self.get_table(str(t.id))

    async def delete_table(self, ref: str) -> dict[str, Any]:
        async with self._sf() as s:
            t = await self._table(s, ref)
            count = await self._row_count(s, t.id)
            name, tid = t.name, str(t.id)
            # explicit child deletes: SQLite in tests doesn't enforce FK cascade
            await s.execute(delete(DbRow).where(DbRow.table_id == t.id))
            await s.execute(delete(DbColumn).where(DbColumn.table_id == t.id))
            await s.delete(t)
            await s.commit()
        if self.on_change is not None:
            await self.on_change(
                {"action": "table.deleted", "table_id": tid, "table": name}
            )
        return {"deleted": name, "rows_deleted": count}

    # ---- columns -------------------------------------------------------

    async def add_column(
        self,
        table_ref: str,
        label: str,
        col_type: str = "text",
        options: dict | None = None,
        required: bool = False,
    ) -> dict[str, Any]:
        async with self._sf() as s:
            t = await self._table(s, table_ref)
            cols = await self._columns(s, t.id)
            if len(cols) >= MAX_COLUMNS_PER_TABLE:
                raise ValueError(f"table '{t.name}' already has {MAX_COLUMNS_PER_TABLE} columns")
            col = self._build_column(
                t.id,
                {"label": label, "type": col_type, "options": options, "required": required},
                position=(max((c.position for c in cols), default=-1) + 1),
                taken={c.key for c in cols},
            )
            s.add(col)
            await s.commit()
            out = _col_dict(col)
        await self._emit("column.added", t, column=out["key"])
        return out

    async def update_column(
        self,
        table_ref: str,
        column_ref: str,
        label: str | None = None,
        col_type: str | None = None,
        options: dict | None = None,
        width: int | None = None,
        position: int | None = None,
        required: bool | None = None,
    ) -> dict[str, Any]:
        async with self._sf() as s:
            t = await self._table(s, table_ref)
            c = await self._column(s, t, column_ref)
            if label is not None and label.strip():
                c.label = label.strip()
            if col_type is not None:
                base_of(col_type)
                c.type = col_type
                c.options = default_options(col_type, options if options is not None else c.options)
            elif options is not None:
                c.options = default_options(c.type, options)
            if width is not None:
                c.width = max(60, min(1200, int(width)))
            if position is not None:
                c.position = int(position)
            if required is not None:
                c.required = bool(required)
            if col_type is not None or options is not None or required is not None:
                rows = list((await s.execute(select(DbRow).where(DbRow.table_id == t.id))).scalars())
                for row in rows:
                    data = dict(row.data or {})
                    current = data.get(c.key)
                    if c.required and current is None:
                        raise CellInvalid(f"column '{c.label}' is required in existing rows")
                    if current is not None:
                        try:
                            data[c.key] = coerce(c.type, current, c.options)
                        except CellInvalid as exc:
                            raise CellInvalid(f"row '{row.id}', column '{c.label}': {exc}") from None
                        row.data = data
            await s.commit()
            out = _col_dict(c)
        await self._emit("column.updated", t, column=out["key"])
        return out

    async def delete_column(self, table_ref: str, column_ref: str) -> dict[str, Any]:
        async with self._sf() as s:
            t = await self._table(s, table_ref)
            c = await self._column(s, t, column_ref)
            key = c.key
            rows = list((await s.execute(select(DbRow).where(DbRow.table_id == t.id))).scalars())
            for row in rows:
                if key in (row.data or {}):
                    row.data = {k: v for k, v in row.data.items() if k != key}
            await s.delete(c)
            await s.commit()
        await self._emit("column.deleted", t, column=key)
        return {"deleted": key, "table": t.name}

    # ---- rows ----------------------------------------------------------

    def _resolve_and_coerce(
        self, cols: list[DbColumn], data: dict, *, partial: bool
    ) -> dict[str, Any]:
        """Map incoming {key-or-label: value} onto column keys and coerce each
        value to its column type. Unknown keys raise with the valid list."""
        by_key = {c.key: c for c in cols}
        by_label = {c.label.lower(): c for c in cols}
        out: dict[str, Any] = {}
        for k, v in (data or {}).items():
            col = by_key.get(k) or by_label.get(str(k).lower())
            if col is None:
                valid = [{"key": c.key, "label": c.label, "type": c.type} for c in cols]
                raise CellInvalid(f"no column '{k}' — valid columns: {valid}")
            try:
                out[col.key] = coerce(col.type, v, col.options)
            except CellInvalid as e:
                raise CellInvalid(f"column '{col.label}': {e}") from None
        if not partial:
            for c in cols:
                if c.required and out.get(c.key) is None:
                    raise CellInvalid(f"column '{c.label}' is required")
        return out

    async def insert_rows(self, table_ref: str, rows: list[dict]) -> dict[str, Any]:
        if not rows:
            return {"inserted": 0, "rows": []}
        if len(rows) > MAX_BULK_ROWS:
            raise ValueError(f"at most {MAX_BULK_ROWS} rows per call (got {len(rows)})")
        async with self._sf() as s:
            t = await self._table(s, table_ref)
            cols = await self._columns(s, t.id)
            count = await self._row_count(s, t.id)
            free = MAX_ROWS_PER_TABLE - count
            if free <= 0:
                raise ValueError(
                    f"table '{t.name}' is full ({MAX_ROWS_PER_TABLE} rows), 0 slots free"
                )
            if len(rows) > free:
                raise ValueError(
                    f"table '{t.name}' has only {free} free slots of {MAX_ROWS_PER_TABLE}; "
                    f"got {len(rows)} rows — insert at most {free}"
                )
            max_pos = (
                await s.execute(
                    select(func.max(DbRow.position)).where(DbRow.table_id == t.id)
                )
            ).scalar_one() or 0.0
            created: list[DbRow] = []
            for i, raw in enumerate(rows):
                data = self._resolve_and_coerce(cols, raw, partial=False)
                r = DbRow(table_id=t.id, data=data, position=max_pos + i + 1)
                s.add(r)
                created.append(r)
            # The expression is evaluated by the database during flush, after
            # any concurrent writer has committed. Starting from the actual
            # count also reconciles pre-0.1.1 or directly imported rows.
            greatest = func.greatest if s.bind.dialect.name == "postgresql" else func.max
            t.row_reservation = greatest(DbTable.row_reservation, count) + len(created)
            table_name = t.name
            try:
                await s.commit()
            except IntegrityError as exc:
                await s.rollback()
                if "row_reservation" not in str(exc.orig):
                    raise
                raise ValueError(
                    f"table '{table_name}' is full ({MAX_ROWS_PER_TABLE} rows), 0 slots free"
                ) from None
            out = [_row_dict(r) for r in created]
            committed_count = await self._row_count(s, t.id)
        await self._emit("rows.inserted", t, row_ids=[r["id"] for r in out])
        return {"inserted": len(out), "rows": out, "row_count": committed_count}

    async def update_rows(self, table_ref: str, updates: list[dict]) -> dict[str, Any]:
        if len(updates) > MAX_BULK_ROWS:
            raise ValueError(f"at most {MAX_BULK_ROWS} updates per call (got {len(updates)})")
        async with self._sf() as s:
            t = await self._table(s, table_ref)
            cols = await self._columns(s, t.id)
            keys = {c.key for c in cols}
            pending: dict[_uuid.UUID, tuple[DbRow, dict[str, Any], dict[str, Any]]] = {}
            out = []
            for u in updates:
                rid = u.get("row_id") or u.get("id")
                if not rid:
                    raise ValueError("every update needs a row_id")
                try:
                    row = await s.get(DbRow, _uuid.UUID(str(rid)))
                except ValueError:
                    row = None
                if row is None or row.table_id != t.id:
                    raise RowNotFound(str(rid))
                patch = self._resolve_and_coerce(cols, u.get("data") or {}, partial=True)
                if row.id in pending:
                    merged = dict(pending[row.id][1])
                    changes = dict(pending[row.id][2])
                else:
                    merged = {k: v for k, v in (row.data or {}).items() if k in keys}
                    changes = {k: None for k in (row.data or {}) if k not in keys}
                merged.update(patch)
                changes.update(patch)
                for c in cols:
                    if c.required and merged.get(c.key) is None:
                        raise CellInvalid(f"column '{c.label}' is required")
                pending[row.id] = (row, merged, changes)
                out.append(row)
            for row, _merged, changes in pending.values():
                # A SQL expression merges each patch with the database's
                # current JSON value at flush time, rather than rewriting the
                # stale snapshot read by this session. Both dialects perform
                # this atomically with the row UPDATE.
                if s.bind.dialect.name == "postgresql":
                    expression = DbRow.data
                    for key, value in changes.items():
                        if value is None:
                            expression = expression.op("-")(key)
                    non_null = {key: value for key, value in changes.items() if value is not None}
                    if non_null:
                        expression = expression.op("||")(literal(non_null, type_=JSONB))
                else:
                    expression = func.json_patch(DbRow.data, literal(changes, type_=JSONB))
                row.data = expression
            await s.commit()
            for row in pending.values():
                await s.refresh(row[0])
            result = [_row_dict(r) for r in out]
        await self._emit("rows.updated", t, row_ids=[r["id"] for r in result])
        return {"updated": len(result), "rows": result}

    async def delete_rows(self, table_ref: str, row_ids: list[str]) -> dict[str, Any]:
        async with self._sf() as s:
            t = await self._table(s, table_ref)
            ids = []
            for rid in row_ids:
                try:
                    ids.append(_uuid.UUID(str(rid)))
                except ValueError:
                    raise RowNotFound(str(rid)) from None
            res = await s.execute(
                delete(DbRow).where(DbRow.table_id == t.id, DbRow.id.in_(ids))
            )
            deleted = res.rowcount or 0
            greatest = func.greatest if s.bind.dialect.name == "postgresql" else func.max
            t.row_reservation = greatest(0, DbTable.row_reservation - deleted)
            await s.commit()
        await self._emit("rows.deleted", t, row_ids=[str(i) for i in ids])
        return {"deleted": deleted}

    async def get_row(self, table_ref: str, row_id: str) -> dict[str, Any]:
        async with self._sf() as s:
            t = await self._table(s, table_ref)
            try:
                row = await s.get(DbRow, _uuid.UUID(str(row_id)))
            except ValueError:
                row = None
            if row is None or row.table_id != t.id:
                raise RowNotFound(str(row_id))
            return _row_dict(row)

    # ---- query ---------------------------------------------------------

    async def query_rows(
        self,
        table_ref: str,
        q: str = "",
        filters: list[dict] | None = None,
        match: str = "all",
        sort: dict | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit = max(1, min(1000, int(limit)))
        offset = max(0, int(offset))
        async with self._sf() as s:
            t = await self._table(s, table_ref)
            cols = await self._columns(s, t.id)

            if not q and not filters and not sort:
                # browse path: paginate in SQL
                total = await self._row_count(s, t.id)
                rows = list(
                    (
                        await s.execute(
                            select(DbRow)
                            .where(DbRow.table_id == t.id)
                            .order_by(DbRow.position, DbRow.created_at)
                            .limit(limit)
                            .offset(offset)
                        )
                    ).scalars()
                )
                return {
                    "table": t.name,
                    "rows": [_row_dict(r) for r in rows],
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                }

            rows = list(
                (
                    await s.execute(
                        select(DbRow)
                        .where(DbRow.table_id == t.id)
                        .order_by(DbRow.position, DbRow.created_at)
                    )
                ).scalars()
            )
        matched = [r for r in rows if self._matches(r, cols, q, filters or [], match)]
        if sort:
            matched = self._sorted(matched, cols, sort)
        return {
            "table": t.name,
            "rows": [_row_dict(r) for r in matched[offset : offset + limit]],
            "total": len(matched),
            "limit": limit,
            "offset": offset,
        }

    def _matches(
        self, row: DbRow, cols: list[DbColumn], q: str, filters: list[dict], match: str
    ) -> bool:
        data = row.data or {}
        if q:
            ql = q.lower()
            hay = [
                str(data.get(c.key))
                for c in cols
                if base_of(c.type) in ("text", "status", "date") and data.get(c.key) is not None
            ]
            # status cells store option ids; search their labels too
            for c in cols:
                if c.type == "status" and data.get(c.key) is not None:
                    for choice in (c.options or {}).get("choices", []):
                        if choice.get("id") == data.get(c.key):
                            hay.append(str(choice.get("label", "")))
            if not any(ql in h.lower() for h in hay):
                return False
        if not filters:
            return True
        by_key = {c.key: c for c in cols}
        by_label = {c.label.lower(): c for c in cols}
        results = []
        for f in filters:
            col = by_key.get(f.get("column", "")) or by_label.get(str(f.get("column", "")).lower())
            if col is None:
                raise CellInvalid(
                    f"no column '{f.get('column')}' — valid: {[c.key for c in cols]}"
                )
            results.append(self._test(col, data.get(col.key), f.get("op", "eq"), f.get("value")))
        return any(results) if match == "any" else all(results)

    def _test(self, col: DbColumn, cell: Any, op: str, value: Any) -> bool:
        if op not in ops_for(col.type):
            raise CellInvalid(
                f"op '{op}' not valid for {col.type} column '{col.label}'; valid: {list(ops_for(col.type))}"
            )
        if op == "is_empty":
            return cell is None
        if op == "not_empty":
            return cell is not None
        base = base_of(col.type)
        if base == "bool":
            want = coerce(col.type, value) if value is not None else True
            return bool(cell) == bool(want)
        if base == "status":
            if op == "is_any_of":
                wanted = [coerce(col.type, v, col.options) for v in (value or [])]
                return cell in wanted
            want = coerce(col.type, value, col.options)
            return (cell == want) if op == "is" else (cell != want)
        if base == "number" or col.type == "date":
            if cell is None:
                return False
            try:
                want = coerce(col.type, value, col.options)
            except CellInvalid:
                return False
            if op == "eq":
                return cell == want
            if op == "neq":
                return cell != want
            if op == "gt":
                return cell > want
            if op == "gte":
                return cell >= want
            if op == "lt":
                return cell < want
            if op == "lte":
                return cell <= want
        # text base
        c = "" if cell is None else str(cell)
        w = "" if value is None else str(value)
        if op == "eq":
            return c.lower() == w.lower()
        if op == "neq":
            return c.lower() != w.lower()
        if op == "contains":
            return cell is not None and w.lower() in c.lower()
        if op == "not_contains":
            return cell is None or w.lower() not in c.lower()
        return False  # pragma: no cover

    def _sorted(self, rows: list[DbRow], cols: list[DbColumn], sort: dict) -> list[DbRow]:
        by_key = {c.key: c for c in cols}
        by_label = {c.label.lower(): c for c in cols}
        col = by_key.get(sort.get("column", "")) or by_label.get(str(sort.get("column", "")).lower())
        if col is None:
            raise CellInvalid(
                f"no sort column '{sort.get('column')}' — valid: {[c.key for c in cols]}"
            )
        reverse = str(sort.get("dir", "asc")).lower() in ("desc", "descending")
        base = base_of(col.type)

        def keyfn(r: DbRow):
            v = (r.data or {}).get(col.key)
            empty = v is None
            if base == "number":
                return (empty, 0 if empty else float(v))
            if base == "bool":
                return (empty, 0 if empty else int(bool(v)))
            return (empty, "" if empty else str(v).lower())

        return sorted(rows, key=keyfn, reverse=reverse)

    async def count_rows(
        self, table_ref: str, q: str = "", filters: list[dict] | None = None, match: str = "all"
    ) -> dict[str, Any]:
        res = await self.query_rows(
            table_ref, q=q, filters=filters, match=match, limit=1000, offset=0
        )
        # breakdowns need every matching row, not one page
        async with self._sf() as s:
            t = await self._table(s, table_ref)
            cols = await self._columns(s, t.id)
            rows = list(
                (await s.execute(select(DbRow).where(DbRow.table_id == t.id))).scalars()
            )
        matched = [r for r in rows if self._matches(r, cols, q, filters or [], match)]
        breakdown: dict[str, dict[str, int]] = {}
        for c in cols:
            if c.type != "status":
                continue
            labels = {o["id"]: o["label"] for o in (c.options or {}).get("choices", [])}
            counts: dict[str, int] = {}
            for r in matched:
                v = (r.data or {}).get(c.key)
                label = labels.get(v, "(empty)") if v is not None else "(empty)"
                counts[label] = counts.get(label, 0) + 1
            breakdown[c.label] = counts
        return {"table": res["table"], "count": len(matched), "by_status": breakdown}
