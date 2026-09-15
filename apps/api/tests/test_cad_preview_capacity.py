from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.cad_intake.adapter import ProcessPackageInspection
from app.cad_intake.config import AllowedCadRoot, CadIntakeConfig
from app.cad_intake.contracts import CadIntakeRequest
from app.cad_intake.preview_adapter import ProcessCadPreviewPreparation
from app.cad_intake.preview_contracts import (
    CadBoundarySelection,
    CadDrawingSelection,
    CadPreviewRequest,
)
from app.operations.progress import OperationCancelled


@pytest.mark.parametrize("cancel_waiting", [False, True])
def test_inspection_and_preview_share_one_process_permit(
    tmp_path, monkeypatch, cancel_waiting
):
    config = CadIntakeConfig(
        (AllowedCadRoot("root", "root", tmp_path),),
        tmp_path / "data",
        tmp_path / "converter",
    )
    inspection = ProcessPackageInspection(config)
    preview = ProcessCadPreviewPreparation(config, tmp_path / "db")
    entered, release, waiting, cancel = Event(), Event(), Event(), Event()
    calls = []

    def inspect(*args):
        calls.append("inspect")
        entered.set()
        assert release.wait(3)

    def prepare(*args):
        calls.append("preview")

    def check():
        waiting.set()
        if cancel.is_set():
            raise OperationCancelled("fixture")

    monkeypatch.setattr(inspection, "_inspect", inspect)
    monkeypatch.setattr(preview, "_prepare", prepare)
    selected = CadDrawingSelection(
        path="source.dxf", source_sha256="a" * 64, normalized_sha256="a" * 64
    )
    request = CadPreviewRequest(
        intake_operation_id="intake",
        manifest_sha256="b" * 64,
        source=selected,
        boundary=CadBoundarySelection(**selected.model_dump(), handle="FF"),
    )
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(
            inspection.inspect,
            "first",
            CadIntakeRequest(root_id="root", entry="source.dxf", entry_sha256="a" * 64),
            lambda: None,
            lambda _: None,
        )
        assert entered.wait(2)
        second = pool.submit(preview.prepare, "second", request, check, lambda _: None)
        assert waiting.wait(2)
        assert calls == ["inspect"]
        if cancel_waiting:
            cancel.set()
            with pytest.raises(OperationCancelled):
                second.result(2)
        release.set()
        first.result(2)
        if not cancel_waiting:
            second.result(2)
    assert calls == (["inspect"] if cancel_waiting else ["inspect", "preview"])
