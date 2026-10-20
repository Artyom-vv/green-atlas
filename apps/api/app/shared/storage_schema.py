"""Реляционная схема выпущенных SQLite-хранилищ, без владения соединениями.

Порядок колонок, типы и ограничения сохраняют совместимость существующих баз.
JSON остаётся текстом; доменные DTO и правила публикации принадлежат репозиториям.
"""

from sqlalchemy import (
    Column,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)

from app.operations.policy import ACTIVE_OPERATION_STATUSES

METADATA = MetaData()

# SQLite TEXT PRIMARY KEY исторически допускает NULL; не усиливаем старую схему.
PROJECTS = Table(
    "projects",
    METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("payload", Text, nullable=False),
    Column("projection", Text),
    Column("source", LargeBinary),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("state_version", Integer, nullable=False, server_default=text("1")),
)
PROJECT_EXPORTS = Table(
    "project_exports",
    METADATA,
    Column("project_id", Text, primary_key=True, nullable=False),
    Column("artifact_id", Text, primary_key=True, nullable=False),
    Column("content", LargeBinary, nullable=False),
    Column("created_at", Text, nullable=False),
)
PROJECT_EXPORTS_INDEX = Index(
    "idx_project_exports_project",
    PROJECT_EXPORTS.c.project_id,
    PROJECT_EXPORTS.c.created_at.desc(),
)
PROJECT_RELEASES = Table(
    "project_releases",
    METADATA,
    Column("project_id", Text, primary_key=True, nullable=False),
    Column("release_id", Text, primary_key=True, nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)
PROJECT_MUTATION_RECEIPTS = Table(
    "project_mutation_receipts",
    METADATA,
    Column("project_id", Text, primary_key=True, nullable=False),
    Column("kind", Text, primary_key=True, nullable=False),
    Column("mutation_id", Text, primary_key=True, nullable=False),
    Column("payload", Text, nullable=False),
)
PROJECT_SOURCE_COMPONENTS = Table(
    "project_source_components",
    METADATA,
    Column("project_id", Text, primary_key=True, nullable=False),
    Column("path", Text, primary_key=True, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("content", LargeBinary, nullable=False),
)
PROJECT_DISPLAY_RESOURCES = Table(
    "project_display_resources",
    METADATA,
    Column("project_id", Text, primary_key=True, nullable=False),
    Column("identity", Text, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("bytes", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
)
PREPARED_BINARY_RESOURCES = Table(
    "prepared_binary_resources",
    METADATA,
    Column("key", Text, primary_key=True, nullable=False),
    Column("source_sha256", Text, primary_key=True, nullable=False),
    Column("payload_sha256", Text, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("bytes", Integer, nullable=False),
)
PROJECT_SOURCES = Table(
    "project_sources",
    METADATA,
    Column("project_id", Text, primary_key=True, nullable=True),
    Column("content", LargeBinary, nullable=False),
    Column("content_sha256", Text),
)
PLAN_CHANGES = Table(
    "plan_changes",
    METADATA,
    Column("project_id", Text, primary_key=True, nullable=False),
    Column("ordinal", Integer, primary_key=True, nullable=False),
    Column("change_set_id", Text, nullable=False),
    Column("label", Text, nullable=False),
    Column("before_payload", Text, nullable=False),
    Column("after_payload", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    UniqueConstraint("project_id", "change_set_id"),
)
PLAN_CHANGES_INDEX = Index(
    "idx_plan_changes_project", PLAN_CHANGES.c.project_id, PLAN_CHANGES.c.ordinal
)
PLAN_HISTORY_CURSORS = Table(
    "plan_history_cursors",
    METADATA,
    Column("project_id", Text, primary_key=True, nullable=True),
    Column("cursor", Integer, nullable=False, server_default=text("0")),
)
PLAN_CHANGE_RECEIPTS = Table(
    "plan_change_receipts",
    METADATA,
    Column("project_id", Text, primary_key=True, nullable=False),
    Column("change_set_id", Text, primary_key=True, nullable=False),
    Column("payload", Text, nullable=False),
    Column("status", Text, nullable=False),
)
PROJECT_OPERATIONS = Table(
    "project_operations",
    METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("project_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)
PROJECT_OPERATIONS_LATEST_INDEX = Index(
    "idx_project_operations_latest",
    PROJECT_OPERATIONS.c.project_id,
    PROJECT_OPERATIONS.c.kind,
    PROJECT_OPERATIONS.c.created_at.desc(),
)
PROJECT_OPERATIONS_ACTIVE_INDEX = Index(
    "idx_project_operations_one_active",
    PROJECT_OPERATIONS.c.project_id,
    PROJECT_OPERATIONS.c.kind,
    unique=True,
    sqlite_where=PROJECT_OPERATIONS.c.status.in_(
        tuple(status.value for status in ACTIVE_OPERATION_STATUSES)
    ),
)
