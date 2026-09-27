from concurrent.futures import ThreadPoolExecutor
from threading import Event, RLock
from time import monotonic, sleep

import pytest

from app.cad_intake.adapter import ProcessPackageInspection
from app.cad_intake.application import CadIntakeApplication
from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.contracts import (
    CadIntakeRecord,
    CadIntakeRequest,
    CadPackagePassport,
)
from app.operations.adapters import InMemoryOperationRepository
from app.operations.contracts import OperationKind, OperationStatus
from app.operations.lifecycle import OperationLifecycle
from app.projects.adapters import InMemoryProjectRepository
from app.projects.contracts import Project
from app.shared.identity import random_id, utc_now


@pytest.mark.parametrize("cancel_waiting", [False, True])
def test_two_projects_share_capacity_and_waiting_is_cancellable(
    tmp_path, monkeypatch, cancel_waiting
):
    config = CadIntakeConfig((), tmp_path, None)
    repository = InMemoryProjectRepository()
    lifecycle = OperationLifecycle(
        repository, InMemoryOperationRepository(), RLock(), utc_now, random_id
    )
    applications = [
        CadIntakeApplication(config, lifecycle, ProcessPackageInspection(config))
        for _ in range(2)
    ]
    request = CadIntakeRequest(root_id="test", entry="entry.dxf", entry_sha256="a" * 64)
    operations = [
        lifecycle.start(
            repository.create(Project(name=f"Project {index}")).id,
            OperationKind.INSPECT_CAD_PACKAGE,
            cad_intake=CadIntakeRecord(request=request),
        )
        for index in range(2)
    ]
    entered, release = Event(), Event()
    calls: list[str] = []

    def inspect(self, operation_id, request, check_cancelled):
        calls.append(operation_id)
        if operation_id == operations[0].id:
            entered.set()
            assert release.wait(2)
        check_cancelled()
        return CadPackagePassport(
            root_id=request.root_id,
            entry=request.entry,
            manifest_sha256="b" * 64,
            drawings=[],
            references=[],
            status="requires_review",
            blockers=[],
        )

    monkeypatch.setattr(ProcessPackageInspection, "_inspect", inspect)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(applications[0].run, operations[0].id)
        assert entered.wait(2)
        second = pool.submit(applications[1].run, operations[1].id)
        deadline = monotonic() + 1
        while (
            lifecycle.operations.get(operations[1].id).status == OperationStatus.QUEUED
        ):
            assert monotonic() < deadline
            sleep(0.01)
        assert calls == [operations[0].id]
        assert (
            lifecycle.operations.get(operations[1].id).stage
            == "Ожидаем свободный процесс проверки CAD"
        )
        if cancel_waiting:
            lifecycle.cancel(operations[1].project_id, operations[1].id)
            second.result(1)
        release.set()
        first.result(2)
        second.result(2)
    if cancel_waiting:
        assert calls == [operations[0].id]
        assert (
            lifecycle.operations.get(operations[1].id).status
            == OperationStatus.CANCELLED
        )
    else:
        assert calls == [operation.id for operation in operations]
        assert all(
            lifecycle.operations.get(operation.id).status == OperationStatus.COMPLETED
            for operation in operations
        )
