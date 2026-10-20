"""Домен истории плана: снимки изменений, курсор отмены и точные квитанции.

Запросы SQLAlchemy выполняются на соединении репозитория проектов, чтобы
изменение плана, его ревизия, история и квитанция коммитились вместе.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.schema import CreateIndex, CreateTable

from app.history.contracts import PlanHistoryEntry, PlanHistoryState
from app.planning.contracts import Plan
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project, ProjectStatus
from app.shared.sqlite_sql import sqlite_sql
from app.shared.storage_schema import (
    PLAN_CHANGE_RECEIPTS as receipts,
)
from app.shared.storage_schema import (
    PLAN_CHANGES as changes,
)
from app.shared.storage_schema import (
    PLAN_CHANGES_INDEX,
)
from app.shared.storage_schema import (
    PLAN_HISTORY_CURSORS as cursors,
)
from app.validation.contracts import CalculationInputBasis

BasisReader = Callable[[Project], CalculationInputBasis | None]


class SqliteProjectHistory:
    """Постоянная история плана фиксируется вместе с проектом одной транзакцией."""

    def __init__(
        self,
        repository: SqliteProjectRepository,
        limit: int = 50,
        *,
        basis_reader: BasisReader | None = None,
    ) -> None:
        self.repository = repository
        self.limit = limit
        self._lock: RLock = repository._lock
        # История использует уже настроенное соединение проекта и ту же транзакцию.
        self._connection = repository._connection
        self.basis_reader = basis_reader
        with self._lock, self._connection:
            for table in (changes, cursors, receipts):
                self._connection.execute(
                    *sqlite_sql(CreateTable(table, if_not_exists=True))
                )
            self._connection.execute(
                *sqlite_sql(CreateIndex(PLAN_CHANGES_INDEX, if_not_exists=True))
            )

    def set_basis_reader(self, basis_reader: BasisReader | None) -> None:
        with self._lock:
            self.basis_reader = basis_reader

    def receipt(self, project_id: str, change_set_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                *sqlite_sql(
                    select(receipts.c.payload, receipts.c.status).where(
                        receipts.c.project_id == project_id,
                        receipts.c.change_set_id == change_set_id,
                    )
                )
            ).fetchone()
            return (
                {**json.loads(row["payload"]), "status": row["status"]} if row else None
            )

    def commit(
        self,
        project: Project,
        before: Project,
        label: str,
        change_set_id: str | None = None,
        receipt: dict | None = None,
        *,
        plan_only: bool = False,
    ) -> Project:
        with self.repository.revision_transaction(project):
            cursor = self._cursor(project.id)
            self._connection.execute(
                *sqlite_sql(
                    delete(changes).where(
                        changes.c.project_id == project.id, changes.c.ordinal > cursor
                    )
                )
            )
            # Только владелец плановых команд может обещать отсутствие правок
            # координат. Generic history commit сохраняет прежний полный writer.
            retain_geometry = (
                plan_only
                and project.geometry is before.geometry
                and project.source_geometry is before.source_geometry
            )
            saved = (
                self.repository._write_plan(project)
                if retain_geometry
                else self.repository._write_project(project)
            )
            if receipt is not None:
                if not change_set_id:
                    raise ValueError("A receipt requires a change set id")
                payload = {
                    **receipt,
                    "state_version": saved.state_version,
                    "plan_version": saved.plan.version if saved.plan else None,
                    "committed_at": datetime.now(UTC).isoformat(),
                }
                self._connection.execute(
                    *sqlite_sql(
                        insert(receipts).values(
                            project_id=project.id,
                            change_set_id=change_set_id,
                            payload=json.dumps(
                                payload, ensure_ascii=False, allow_nan=False
                            ),
                            status="applied",
                        )
                    )
                )
            ordinal = cursor + 1
            self._connection.execute(
                *sqlite_sql(
                    insert(changes).values(
                        project_id=project.id,
                        ordinal=ordinal,
                        change_set_id=change_set_id or str(uuid4()),
                        label=label,
                        before_payload=self._snapshot_payload(before),
                        after_payload=self._snapshot_payload(saved),
                        created_at=datetime.now(UTC).isoformat(),
                    )
                )
            )
            position = sqlite_insert(cursors).values(
                project_id=project.id, cursor=ordinal
            )
            self._connection.execute(
                *sqlite_sql(
                    position.on_conflict_do_update(
                        index_elements=[cursors.c.project_id],
                        set_={"cursor": position.excluded.cursor},
                    )
                )
            )
            floor = max(0, ordinal - self.limit)
            if floor:
                self._connection.execute(
                    *sqlite_sql(
                        delete(changes).where(
                            changes.c.project_id == project.id,
                            changes.c.ordinal <= floor,
                        )
                    )
                )
        return saved

    def clear(self, project_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                *sqlite_sql(delete(changes).where(changes.c.project_id == project_id))
            )
            self._connection.execute(
                *sqlite_sql(delete(cursors).where(cursors.c.project_id == project_id))
            )

    def rebase_planting_zones(
        self, project_id: str, zones: list[PlantingZoneAssignment]
    ) -> None:
        serialized = [zone.model_dump(mode="json") for zone in zones]
        with self._lock, self._connection:
            rows = self._connection.execute(
                *sqlite_sql(
                    select(
                        changes.c.ordinal,
                        changes.c.before_payload,
                        changes.c.after_payload,
                    ).where(changes.c.project_id == project_id)
                )
            ).fetchall()
            for row in rows:
                before = json.loads(str(row["before_payload"]))
                after = json.loads(str(row["after_payload"]))
                before["planting_zones"] = serialized
                after["planting_zones"] = serialized
                self._connection.execute(
                    *sqlite_sql(
                        update(changes)
                        .where(
                            changes.c.project_id == project_id,
                            changes.c.ordinal == int(row["ordinal"]),
                        )
                        .values(
                            before_payload=json.dumps(
                                before, ensure_ascii=False, separators=(",", ":")
                            ),
                            after_payload=json.dumps(
                                after, ensure_ascii=False, separators=(",", ":")
                            ),
                        )
                    )
                )

    def state(self, project_id: str) -> PlanHistoryState:
        with self._lock:
            cursor = self._cursor(project_id)
            undo = self._connection.execute(
                *sqlite_sql(
                    select(changes.c.label, changes.c.change_set_id).where(
                        changes.c.project_id == project_id, changes.c.ordinal == cursor
                    )
                )
            ).fetchone()
            redo = self._connection.execute(
                *sqlite_sql(
                    select(changes.c.label, changes.c.change_set_id).where(
                        changes.c.project_id == project_id,
                        changes.c.ordinal == cursor + 1,
                    )
                )
            ).fetchone()
            rows = self._connection.execute(
                *sqlite_sql(
                    select(
                        changes.c.ordinal,
                        changes.c.change_set_id,
                        changes.c.label,
                        changes.c.created_at,
                    )
                    .where(changes.c.project_id == project_id)
                    .order_by(changes.c.ordinal.desc())
                    .limit(20)
                )
            ).fetchall()
        return PlanHistoryState(
            cursor_ordinal=cursor,
            undo_target_id=str(undo["change_set_id"]) if undo is not None else None,
            redo_target_id=str(redo["change_set_id"]) if redo is not None else None,
            can_undo=undo is not None,
            can_redo=redo is not None,
            undo_label=str(undo["label"]) if undo is not None else None,
            redo_label=str(redo["label"]) if redo is not None else None,
            entries=[
                PlanHistoryEntry(
                    id=str(row["change_set_id"]),
                    ordinal=int(row["ordinal"]),
                    label=str(row["label"]),
                    created_at=str(row["created_at"]),
                    applied=int(row["ordinal"]) <= cursor,
                )
                for row in rows
            ],
        )

    def undo(self, project: Project) -> Project:
        with self.repository.revision_transaction(project):
            cursor = self._cursor(project.id)
            row = self._connection.execute(
                *sqlite_sql(
                    select(changes.c.before_payload).where(
                        changes.c.project_id == project.id, changes.c.ordinal == cursor
                    )
                )
            ).fetchone()
            if row is None:
                raise ValueError("Нет изменений для отмены")
            self._restore(project, str(row["before_payload"]))
            # _restore меняет только план, статус и проверенные прежние зоны.
            saved = self.repository._write_plan(project)
            self._connection.execute(
                *sqlite_sql(
                    update(receipts)
                    .where(
                        receipts.c.project_id == project.id,
                        receipts.c.change_set_id.in_(
                            select(changes.c.change_set_id).where(
                                changes.c.project_id == project.id,
                                changes.c.ordinal == cursor,
                            )
                        ),
                    )
                    .values(status="undone")
                )
            )
            self._connection.execute(
                *sqlite_sql(
                    update(cursors)
                    .where(cursors.c.project_id == project.id)
                    .values(cursor=cursor - 1)
                )
            )
        return saved

    def redo(self, project: Project) -> Project:
        with self.repository.revision_transaction(project):
            cursor = self._cursor(project.id)
            row = self._connection.execute(
                *sqlite_sql(
                    select(changes.c.after_payload).where(
                        changes.c.project_id == project.id,
                        changes.c.ordinal == cursor + 1,
                    )
                )
            ).fetchone()
            if row is None:
                raise ValueError("Нет изменений для повтора")
            self._restore(project, str(row["after_payload"]))
            saved = self.repository._write_plan(project)
            self._connection.execute(
                *sqlite_sql(
                    update(receipts)
                    .where(
                        receipts.c.project_id == project.id,
                        receipts.c.change_set_id.in_(
                            select(changes.c.change_set_id).where(
                                changes.c.project_id == project.id,
                                changes.c.ordinal == cursor + 1,
                            )
                        ),
                    )
                    .values(status="applied")
                )
            )
            self._connection.execute(
                *sqlite_sql(
                    update(cursors)
                    .where(cursors.c.project_id == project.id)
                    .values(cursor=cursor + 1)
                )
            )
        return saved

    @staticmethod
    def _snapshot_payload(project: Project) -> str:
        return json.dumps(
            {
                "planting_zones": [
                    zone.model_dump(mode="json") for zone in project.planting_zones
                ],
                "plan": project.plan.model_dump(mode="json") if project.plan else None,
                "status": project.status.value,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _restore(self, project: Project, payload: str) -> Project:
        value = json.loads(payload)
        zones = [
            PlantingZoneAssignment.model_validate(item)
            for item in value["planting_zones"]
        ]
        if project.planting_zones != zones:
            raise ValueError(
                "История плана относится к прежним участкам. Обновите историю перед отменой или повтором."
            )
        project.planting_zones = zones
        previous_version = project.plan.version if project.plan else 0
        project.plan = (
            Plan.model_validate(value["plan"]) if value["plan"] is not None else None
        )
        _advance_restored_plan(project, previous_version)
        project.status = ProjectStatus(value["status"])
        _invalidate_restored_basis(project, self.basis_reader)
        return project

    def _cursor(self, project_id: str) -> int:
        row = self._connection.execute(
            *sqlite_sql(
                select(cursors.c.cursor).where(cursors.c.project_id == project_id)
            )
        ).fetchone()
        return int(row["cursor"]) if row is not None else 0


def _advance_restored_plan(project: Project, previous_version: int) -> None:
    if project.plan is None:
        return
    # Восстановление является новой правкой; старая версия предпросмотра не переиспользуется.
    project.plan.version = previous_version + 1
    if project.plan.validation_basis is not None:
        project.plan.validation_basis = project.plan.validation_basis.model_copy(
            update={"plan_version": project.plan.version}
        )


def _invalidate_restored_basis(
    project: Project, basis_reader: BasisReader | None
) -> None:
    plan = project.plan
    if plan is None or plan.validation_basis is None:
        return
    stored = plan.validation_basis
    current = basis_reader(project) if basis_reader else None
    if (
        current is None
        or stored.geometry_version != project.geometry_version
        or stored.geometry_digest != current.geometry_digest
        or stored.calculation_digest != current.calculation_digest
        or stored.evidence_digest != current.evidence_digest
    ):
        # Замечания остаются историческими. Восстановление объектов само по себе
        # не делает проверку действующей при неизвестном расчётном основании.
        plan.validation_basis = None
