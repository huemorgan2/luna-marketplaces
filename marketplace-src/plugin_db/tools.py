"""Agent-facing tools for plugin-db. Thin handlers over DbStore. Writes touch
only plugin-owned tables, so reads/writes are auto_approve; destructive
deletes (table / column / rows) prompt.

Errors are returned, not raised — always with a recovery hint (the table
list, the valid columns, the free-slot count) so the agent can self-correct.
"""

from __future__ import annotations

from typing import Any

from luna_sdk import PluginContext, ToolDef

from .store import CellInvalid, ColumnNotFound, DbStore, RowNotFound, TableNotFound

_TABLE_PARAM = {
    "type": "string",
    "description": "Table name (case-insensitive) or table id — see db_list_tables.",
}

_COLUMN_TYPES_DOC = (
    "text | number | status | date | checkbox | email | phone | address | currency | percent"
)

_COLUMN_SPEC = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "description": "Column header shown in the board."},
        "type": {"type": "string", "description": f"One of: {_COLUMN_TYPES_DOC}. Default text."},
        "options": {
            "type": "object",
            "description": (
                "Per-type config. status: {choices:[{label,color?}]} (omit for "
                "Todo/Working on it/Done/Stuck). currency: {symbol:'$',position:'prefix'|'suffix'}. "
                "percent: {decimals}."
            ),
        },
        "required": {"type": "boolean"},
    },
    "required": ["label"],
}

_FILTERS_PARAM = {
    "type": "array",
    "description": (
        "Structured conditions, e.g. [{column:'status',op:'is',value:'done'}]. Ops by type — "
        "text-ish: eq/neq/contains/not_contains/is_empty/not_empty; number/date/currency/percent: "
        "eq/neq/gt/gte/lt/lte/is_empty/not_empty; status: is/is_not/is_any_of/is_empty/not_empty; "
        "checkbox: is. db_get_table lists each column's valid ops."
    ),
    "items": {
        "type": "object",
        "properties": {
            "column": {"type": "string", "description": "Column key or label."},
            "op": {"type": "string"},
            "value": {"description": "Comparison value (array for is_any_of)."},
        },
        "required": ["column", "op"],
    },
}


def register_tools(ctx: PluginContext, store: DbStore) -> None:
    async def _no_such_table(ref: str) -> dict[str, Any]:
        return {"error": f"no table '{ref}'", "tables": await store.list_tables()}

    async def _no_such_column(table: str, ref: str) -> dict[str, Any]:
        t = await store.get_table(table)
        return {"error": f"no column '{ref}' in table '{t['name']}'", "columns": t["columns"]}

    async def _list_tables() -> dict[str, Any]:
        return {"tables": await store.list_tables()}

    async def _get_table(table: str) -> dict[str, Any]:
        try:
            return await store.get_table(table)
        except TableNotFound:
            return await _no_such_table(table)

    async def _create_table(
        name: str, description: str = "", columns: list[dict] | None = None
    ) -> dict[str, Any]:
        try:
            return await store.create_table(name, description=description, columns=columns)
        except (ValueError, CellInvalid) as e:
            return {"error": str(e), "tables": await store.list_tables()}

    async def _rename_table(
        table: str, name: str | None = None, description: str | None = None
    ) -> dict[str, Any]:
        try:
            return await store.rename_table(table, name=name, description=description)
        except TableNotFound:
            return await _no_such_table(table)
        except ValueError as e:
            return {"error": str(e)}

    async def _delete_table(table: str) -> dict[str, Any]:
        try:
            return await store.delete_table(table)
        except TableNotFound:
            return await _no_such_table(table)

    async def _add_column(
        table: str,
        label: str,
        type: str = "text",  # noqa: A002 - tool arg name
        options: dict | None = None,
        required: bool = False,
    ) -> dict[str, Any]:
        try:
            return await store.add_column(
                table, label, col_type=type, options=options, required=required
            )
        except TableNotFound:
            return await _no_such_table(table)
        except (ValueError, CellInvalid) as e:
            return {"error": str(e), "types": _COLUMN_TYPES_DOC}

    async def _update_column(
        table: str,
        column: str,
        label: str | None = None,
        type: str | None = None,  # noqa: A002
        options: dict | None = None,
        position: int | None = None,
        required: bool | None = None,
    ) -> dict[str, Any]:
        try:
            return await store.update_column(
                table,
                column,
                label=label,
                col_type=type,
                options=options,
                position=position,
                required=required,
            )
        except TableNotFound:
            return await _no_such_table(table)
        except ColumnNotFound:
            return await _no_such_column(table, column)
        except (ValueError, CellInvalid) as e:
            return {"error": str(e), "types": _COLUMN_TYPES_DOC}

    async def _delete_column(table: str, column: str) -> dict[str, Any]:
        try:
            return await store.delete_column(table, column)
        except TableNotFound:
            return await _no_such_table(table)
        except ColumnNotFound:
            return await _no_such_column(table, column)

    async def _insert_rows(table: str, rows: list[dict]) -> dict[str, Any]:
        try:
            return await store.insert_rows(table, rows)
        except TableNotFound:
            return await _no_such_table(table)
        except (ValueError, CellInvalid) as e:
            return {"error": str(e)}

    async def _update_rows(table: str, updates: list[dict]) -> dict[str, Any]:
        try:
            return await store.update_rows(table, updates)
        except TableNotFound:
            return await _no_such_table(table)
        except RowNotFound as e:
            return {"error": f"no row '{e}' in table '{table}'"}
        except (ValueError, CellInvalid) as e:
            return {"error": str(e)}

    async def _delete_rows(table: str, row_ids: list[str]) -> dict[str, Any]:
        try:
            return await store.delete_rows(table, row_ids)
        except TableNotFound:
            return await _no_such_table(table)
        except RowNotFound as e:
            return {"error": f"'{e}' is not a valid row id"}

    async def _get_row(table: str, row_id: str) -> dict[str, Any]:
        try:
            return await store.get_row(table, row_id)
        except TableNotFound:
            return await _no_such_table(table)
        except RowNotFound:
            return {"error": f"no row '{row_id}' in table '{table}'"}

    async def _query(
        table: str,
        q: str = "",
        filters: list[dict] | None = None,
        match: str = "all",
        sort: dict | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        try:
            return await store.query_rows(
                table, q=q, filters=filters, match=match, sort=sort, limit=limit, offset=offset
            )
        except TableNotFound:
            return await _no_such_table(table)
        except CellInvalid as e:
            return {"error": str(e)}

    async def _count(
        table: str, q: str = "", filters: list[dict] | None = None, match: str = "all"
    ) -> dict[str, Any]:
        try:
            return await store.count_rows(table, q=q, filters=filters, match=match)
        except TableNotFound:
            return await _no_such_table(table)
        except CellInvalid as e:
            return {"error": str(e)}

    defs: list[tuple[ToolDef, Any]] = [
        (
            ToolDef(
                name="db_list_tables",
                description=(
                    "List every data table: id, name, description, row count. Start "
                    "here — every other db_* tool takes a table name or id from this list."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            _list_tables,
        ),
        (
            ToolDef(
                name="db_get_table",
                description=(
                    "A table's full schema: columns (key, label, type, options), row "
                    "count, free slots (of 10,000), and each column's valid filter ops."
                ),
                parameters={
                    "type": "object",
                    "properties": {"table": _TABLE_PARAM},
                    "required": ["table"],
                },
            ),
            _get_table,
        ),
        (
            ToolDef(
                name="db_create_table",
                description=(
                    "Create a data table with typed columns. Types: "
                    f"{_COLUMN_TYPES_DOC}. Omit `columns` for a monday-style default "
                    "(a text 'Name' column + a Status column with Todo/Working on it/"
                    "Done/Stuck). Tables hold up to 10,000 rows."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "columns": {"type": "array", "items": _COLUMN_SPEC},
                    },
                    "required": ["name"],
                },
            ),
            _create_table,
        ),
        (
            ToolDef(
                name="db_rename_table",
                description="Rename a table or update its description.",
                parameters={
                    "type": "object",
                    "properties": {
                        "table": _TABLE_PARAM,
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["table"],
                },
            ),
            _rename_table,
        ),
        (
            ToolDef(
                name="db_delete_table",
                description="Permanently delete a table with all its columns and rows.",
                parameters={
                    "type": "object",
                    "properties": {"table": _TABLE_PARAM},
                    "required": ["table"],
                },
                policy="prompt_always",
                risk_level="high",
            ),
            _delete_table,
        ),
        (
            ToolDef(
                name="db_add_column",
                description=f"Add a column to a table. Types: {_COLUMN_TYPES_DOC}.",
                parameters={
                    "type": "object",
                    "properties": {
                        "table": _TABLE_PARAM,
                        "label": {"type": "string"},
                        "type": {"type": "string", "description": f"One of: {_COLUMN_TYPES_DOC}."},
                        "options": _COLUMN_SPEC["properties"]["options"],
                        "required": {"type": "boolean"},
                    },
                    "required": ["table", "label"],
                },
            ),
            _add_column,
        ),
        (
            ToolDef(
                name="db_update_column",
                description=(
                    "Change a column: rename (label), retype, replace options (e.g. "
                    "status choices), reorder, toggle required. Existing cell values "
                    "are kept as-is; values that no longer fit the type surface as "
                    "cell errors rather than being destroyed."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "table": _TABLE_PARAM,
                        "column": {"type": "string", "description": "Column key, label, or id."},
                        "label": {"type": "string"},
                        "type": {"type": "string", "description": f"One of: {_COLUMN_TYPES_DOC}."},
                        "options": _COLUMN_SPEC["properties"]["options"],
                        "position": {"type": "integer"},
                        "required": {"type": "boolean"},
                    },
                    "required": ["table", "column"],
                },
            ),
            _update_column,
        ),
        (
            ToolDef(
                name="db_delete_column",
                description="Delete a column from a table (its cell values become unreachable).",
                parameters={
                    "type": "object",
                    "properties": {
                        "table": _TABLE_PARAM,
                        "column": {"type": "string", "description": "Column key, label, or id."},
                    },
                    "required": ["table", "column"],
                },
                policy="prompt_always",
                risk_level="high",
            ),
            _delete_column,
        ),
        (
            ToolDef(
                name="db_insert_rows",
                description=(
                    "Insert up to 500 rows. Each row is {column key or label: value}. "
                    "Values are validated per column type (status accepts a choice id "
                    "or label; date is YYYY-MM-DD). Fails with the free-slot count if "
                    "the 10,000-row cap would be exceeded."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "table": _TABLE_PARAM,
                        "rows": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Row objects: {column key or label: value}.",
                        },
                    },
                    "required": ["table", "rows"],
                },
            ),
            _insert_rows,
        ),
        (
            ToolDef(
                name="db_update_rows",
                description=(
                    "Update cells on existing rows: [{row_id, data:{column: value}}]. "
                    "Only the given columns change; set a value to null to clear it."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "table": _TABLE_PARAM,
                        "updates": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "row_id": {"type": "string"},
                                    "data": {"type": "object"},
                                },
                                "required": ["row_id", "data"],
                            },
                        },
                    },
                    "required": ["table", "updates"],
                },
            ),
            _update_rows,
        ),
        (
            ToolDef(
                name="db_delete_rows",
                description="Delete rows by id.",
                parameters={
                    "type": "object",
                    "properties": {
                        "table": _TABLE_PARAM,
                        "row_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["table", "row_ids"],
                },
                policy="ask",
                risk_level="high",
            ),
            _delete_rows,
        ),
        (
            ToolDef(
                name="db_get_row",
                description="Read one row by id.",
                parameters={
                    "type": "object",
                    "properties": {"table": _TABLE_PARAM, "row_id": {"type": "string"}},
                    "required": ["table", "row_id"],
                },
            ),
            _get_row,
        ),
        (
            ToolDef(
                name="db_query",
                description=(
                    "Search / filter / sort rows of one table with pagination. `q` is "
                    "a free-text contains-search across text-ish columns (status "
                    "labels included). Combine with structured `filters` (match: "
                    "all|any), `sort` {column, dir:asc|desc}, `limit`/`offset`. "
                    "Returns rows + total match count."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "table": _TABLE_PARAM,
                        "q": {"type": "string"},
                        "filters": _FILTERS_PARAM,
                        "match": {"type": "string", "enum": ["all", "any"]},
                        "sort": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string"},
                                "dir": {"type": "string", "enum": ["asc", "desc"]},
                            },
                            "required": ["column"],
                        },
                        "limit": {"type": "integer", "default": 200},
                        "offset": {"type": "integer", "default": 0},
                    },
                    "required": ["table"],
                },
            ),
            _query,
        ),
        (
            ToolDef(
                name="db_count",
                description=(
                    "Count rows matching a search/filter, with a per-status breakdown "
                    "for every status column (e.g. how many Done vs Stuck)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "table": _TABLE_PARAM,
                        "q": {"type": "string"},
                        "filters": _FILTERS_PARAM,
                        "match": {"type": "string", "enum": ["all", "any"]},
                    },
                    "required": ["table"],
                },
            ),
            _count,
        ),
    ]

    for tool_def, handler in defs:
        ctx.tool_registry.register("plugin-db", tool_def, handler)
