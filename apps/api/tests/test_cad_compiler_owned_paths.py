"""Native-импорт: прежние байты и подписи при переиспользовании допущенных контуров."""

from __future__ import annotations

import json
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from test_cad_bridge_compiler import (
    near_closed_building_proposal_probe,
    paired_native_region_probe,
    probe_with_native_primitives,
)

from app.cad_bridge import compiler
from app.cad_bridge.contracts import PathGeometry
from app.cad_bridge.provider import (
    CadSnapshotProviderError,
    verify_cad_snapshot_integrity,
)


@pytest.mark.parametrize(
    ("probe_factory", "region_sha256", "live_sha256"),
    [
        (
            probe_with_native_primitives,
            "bfa25cfff45daa09983f08234dbaa1956ca5259ff36aabc3ef1d1397f038732d",
            "ec67fc62c5438656e0ae6e55d36f695b4d82a761d351754689f84d5d45064951",
        ),
        (
            near_closed_building_proposal_probe,
            "519dd8e592872c7f929a43764746dd586e8848b4ca7ac44df3916d035fcb24b8",
            "624d1f8138406e850793b8082074fe8baa1aa9b706b382d6bba4f9fed9686418",
        ),
        (
            paired_native_region_probe,
            "ed677bd1a57cf5792dc4a5050f63636ff29e5b249849081bd7a07622f68d7511",
            "f02536db28bad6d7be809811649486f25065e14f2e09efef3b6b3e26c395de05",
        ),
    ],
)
def test_owned_paths_preserve_previous_snapshot_bytes_and_signatures(
    tmp_path: Path,
    probe_factory: Callable[[], dict[str, Any]],
    region_sha256: str,
    live_sha256: str,
) -> None:
    # Хеши зафиксированы на предыдущем компиляторе. Включают весь DTO,
    # подписи записей, payload и связи открытых контуров с предложениями областей.
    probe = probe_factory()
    snapshot = compiler.compile_region_probe(
        probe, autocad_version="2027", target="macos-arm64"
    )
    assert (
        sha256(snapshot.model_dump_json(by_alias=True).encode()).hexdigest()
        == region_sha256
    )
    verify_cad_snapshot_integrity(snapshot)

    probe["capture_mode"] = "live_document"
    content = json.dumps(probe, ensure_ascii=False).encode()
    source = tmp_path / "capture.json"
    source.write_bytes(content)
    from_bytes = compiler.compile_live_document(
        content, autocad_version="2027", target="macos-arm64"
    )
    from_file, source_size = compiler.compile_live_document_file(
        source,
        expected_source_sha256=sha256(content).hexdigest(),
        autocad_version="2027",
        target="macos-arm64",
    )
    assert from_file == from_bytes
    assert source_size == len(content)
    assert source.read_bytes() == content
    assert (
        sha256(from_file.model_dump_json(by_alias=True).encode()).hexdigest()
        == live_sha256
    )
    verify_cad_snapshot_integrity(from_file)


def test_owned_path_mutation_still_fails_use_time_integrity() -> None:
    snapshot = compiler.compile_region_probe(
        probe_with_native_primitives(), autocad_version="2027", target="macos-arm64"
    )
    path = next(item for item in snapshot.geometry if isinstance(item, PathGeometry))
    path.coordinates[1] = (999, 0, 150.1)
    with pytest.raises(CadSnapshotProviderError, match="geometry hash differs"):
        verify_cad_snapshot_integrity(snapshot)
