"""SQLAlchemy rows for plugin-db — user-defined tables as data, not DDL.

One luna-table = one row in `db_tables` (identity/metadata only), its schema =
rows in `db_columns`, its data = rows in `db_rows` with the cell values in a
single JSONB keyed by column key. Column add/rename/retype are metadata
updates; no runtime CREATE/ALTER TABLE ever runs against the catalog.

All tables are created idempotently in on_load; the Postgres-only GIN index on
db_rows.data is added there behind a dialect guard (SQLite dev runs without it
— at the 10k rows/table cap that's a non-issue).
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from luna_sdk import JSONB, UUID, declarative_base

Base = declarative_base()

MAX_ROWS_PER_TABLE = 10_000
MAX_COLUMNS_PER_TABLE = 100
MAX_CELL_CHARS = 10_000
MAX_BULK_ROWS = 500


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DbTable(Base):
    """One user-facing luna-table. Holds identity only — columns and rows live
    in their own tables, linked by table_id, and cascade away on delete."""

    __tablename__ = "db_tables"

    id: Mapped[_uuid.UUID] = mapped_column(UUID(), primary_key=True, default=_uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Serialized capacity reservation. SQL increments happen at commit/flush,
    # so two writers cannot both acknowledge the last free slot.
    row_reservation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (CheckConstraint("row_reservation <= 10000", name="ck_db_tables_row_reservation"),)


class DbColumn(Base):
    """One column of a luna-table. `key` is the stable slug used as the JSONB
    field name in db_rows.data — renaming changes only `label`. `options` is
    per-type config (status choices+colors, currency symbol, …); `width` is
    the remembered UI column width in px (NULL = auto)."""

    __tablename__ = "db_columns"

    id: Mapped[_uuid.UUID] = mapped_column(UUID(), primary_key=True, default=_uuid.uuid4)
    table_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(), ForeignKey("db_tables.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    type: Mapped[str] = mapped_column(String(32), default="text", nullable=False)
    options: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (UniqueConstraint("table_id", "key", name="uq_db_columns_table_key"),)


class DbRow(Base):
    """One data row of a luna-table. `data` holds just this row's cells
    ({column_key: value}) so an edit rewrites ~bytes, not the whole table.
    `position` is fractional for cheap manual reordering."""

    __tablename__ = "db_rows"

    id: Mapped[_uuid.UUID] = mapped_column(UUID(), primary_key=True, default=_uuid.uuid4)
    table_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(), ForeignKey("db_tables.id", ondelete="CASCADE"), nullable=False, index=True
    )
    data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    position: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


# FK parents first
ALL_TABLES = (DbTable.__table__, DbColumn.__table__, DbRow.__table__)
