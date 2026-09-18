"""plugin-db API routes — data REST + the board pane UI.

Mounted at /api/p/plugin-db/* by the loader via manifest.routes_module.
Decoupled to `luna_sdk` — no `import luna.*`. Data routes are auth-gated;
/ui/ is served unauthenticated (the Shell iframes it with no header; the app
inside fetches data with the token it gets via postMessage).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .store import CellInvalid, ColumnNotFound, DbStore, RowNotFound, TableNotFound

_UI_DIR = Path(__file__).parent / "ui"
_NO_CACHE = {"Cache-Control": "no-store"}


# Module level, NOT inside register_routes: with `from __future__ import
# annotations` FastAPI resolves string annotations from module globals — a
# function-local class silently degrades to a query param.
class TableCreate(BaseModel):
    name: str
    description: str = ""
    columns: list[dict] | None = None


class TablePatch(BaseModel):
    name: str | None = None
    description: str | None = None


class ColumnCreate(BaseModel):
    label: str
    type: str = "text"
    options: dict | None = None
    required: bool = False


class ColumnPatch(BaseModel):
    label: str | None = None
    type: str | None = None
    options: dict | None = None
    width: int | None = None
    position: int | None = None
    required: bool | None = None


class RowsCreate(BaseModel):
    rows: list[dict]


class RowPatch(BaseModel):
    data: dict


class RowsDelete(BaseModel):
    row_ids: list[str]


class Query(BaseModel):
    q: str = ""
    filters: list[dict] | None = None
    match: str = "all"
    sort: dict | None = None
    limit: int = 200
    offset: int = 0


def _wrap(exc: Exception) -> HTTPException:
    if isinstance(exc, TableNotFound):
        return HTTPException(404, f"no table '{exc}'")
    if isinstance(exc, ColumnNotFound):
        return HTTPException(404, f"no column '{exc}'")
    if isinstance(exc, RowNotFound):
        return HTTPException(404, f"no row '{exc}'")
    return HTTPException(422, str(exc))


def register_routes(app, ctx):
    from luna_sdk import get_current_user

    store = DbStore(ctx.db_session_factory)

    async def _forward(evt: dict) -> None:
        await ctx.events.emit("db.updated", evt)

    store.on_change = _forward
    router = APIRouter(prefix="/api/p/plugin-db", tags=["db"])

    def _run(coro):
        async def wrapper():
            try:
                return await coro
            except (TableNotFound, ColumnNotFound, RowNotFound, ValueError, CellInvalid) as e:
                raise _wrap(e) from e

        return wrapper()

    @router.get("/tables")
    async def list_tables(user=Depends(get_current_user)) -> Any:
        return await store.list_tables()

    @router.post("/tables")
    async def create_table(body: TableCreate, user=Depends(get_current_user)) -> Any:
        return await _run(
            store.create_table(body.name, description=body.description, columns=body.columns)
        )

    @router.get("/tables/{ref}")
    async def get_table(ref: str, user=Depends(get_current_user)) -> Any:
        return await _run(store.get_table(ref))

    @router.patch("/tables/{ref}")
    async def patch_table(ref: str, body: TablePatch, user=Depends(get_current_user)) -> Any:
        return await _run(store.rename_table(ref, name=body.name, description=body.description))

    @router.delete("/tables/{ref}")
    async def delete_table(ref: str, user=Depends(get_current_user)) -> Any:
        return await _run(store.delete_table(ref))

    @router.post("/tables/{ref}/columns")
    async def add_column(ref: str, body: ColumnCreate, user=Depends(get_current_user)) -> Any:
        return await _run(
            store.add_column(
                ref, body.label, col_type=body.type, options=body.options, required=body.required
            )
        )

    @router.patch("/tables/{ref}/columns/{column}")
    async def patch_column(
        ref: str, column: str, body: ColumnPatch, user=Depends(get_current_user)
    ) -> Any:
        return await _run(
            store.update_column(
                ref,
                column,
                label=body.label,
                col_type=body.type,
                options=body.options,
                width=body.width,
                position=body.position,
                required=body.required,
            )
        )

    @router.delete("/tables/{ref}/columns/{column}")
    async def delete_column(ref: str, column: str, user=Depends(get_current_user)) -> Any:
        return await _run(store.delete_column(ref, column))

    @router.post("/tables/{ref}/rows")
    async def insert_rows(ref: str, body: RowsCreate, user=Depends(get_current_user)) -> Any:
        return await _run(store.insert_rows(ref, body.rows))

    @router.patch("/tables/{ref}/rows/{row_id}")
    async def patch_row(
        ref: str, row_id: str, body: RowPatch, user=Depends(get_current_user)
    ) -> Any:
        return await _run(store.update_rows(ref, [{"row_id": row_id, "data": body.data}]))

    @router.post("/tables/{ref}/rows/delete")
    async def delete_rows(ref: str, body: RowsDelete, user=Depends(get_current_user)) -> Any:
        return await _run(store.delete_rows(ref, body.row_ids))

    @router.post("/tables/{ref}/query")
    async def query(ref: str, body: Query, user=Depends(get_current_user)) -> Any:
        return await _run(
            store.query_rows(
                ref,
                q=body.q,
                filters=body.filters,
                match=body.match,
                sort=body.sort,
                limit=body.limit,
                offset=body.offset,
            )
        )

    # --- Board pane UI (unauthenticated static; see module docstring) ----

    def _versioned_index() -> Response:
        html = (_UI_DIR / "index.html").read_text()
        try:
            import tomllib

            _v = str(
                tomllib.loads((Path(__file__).parent / "luna-plugin.toml").read_text())["version"]
            )
        except Exception:  # noqa: BLE001
            _v = "0"
        html = html.replace('.js"', f'.js?v={_v}"').replace('.css"', f'.css?v={_v}"')
        return Response(content=html, media_type="text/html", headers=_NO_CACHE)

    @router.get("/ui/")
    async def serve_ui_root():
        if (_UI_DIR / "index.html").exists():
            return _versioned_index()
        raise HTTPException(404, "UI not built")

    @router.get("/ui/{path:path}")
    async def serve_ui(path: str):
        if not path or path == "/":
            path = "index.html"
        target = (_UI_DIR / path).resolve()
        if not target.is_relative_to(_UI_DIR.resolve()):
            raise HTTPException(403, "Forbidden")
        if not target.exists():
            if (_UI_DIR / "index.html").exists():
                return _versioned_index()
            raise HTTPException(404, "Not found")
        return FileResponse(str(target), headers=_NO_CACHE)

    app.include_router(router)
