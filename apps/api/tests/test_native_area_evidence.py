from collections import OrderedDict
from hashlib import sha256
from unittest.mock import patch

import pytest
from test_autocad_live_import import live_area_probe, live_bytes

from app.cad_bridge.compiler import compile_live_document
from app.dxf_import.review import evidence as module
from app.shared.failures import DomainFailure, FailureReason


def test_admits_once_caches_only_small_evidence_and_still_rejects_changed_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_cache", OrderedDict())
    content = live_bytes(live_area_probe())
    digest = sha256(content).hexdigest()
    with patch.object(
        module, "compile_live_document", wraps=compile_live_document
    ) as compile:
        first = module.area_evidence(
            content,
            source_sha256=digest,
            autocad_version="2027.0.1",
            target="macos-arm64",
        )
        first.proposals[0].layer = "tampered cache consumer"
        second = module.area_evidence(
            content,
            source_sha256=digest,
            autocad_version="2027.0.1",
            target="macos-arm64",
        )
        assert compile.call_count == 1
        assert second.proposals[0].layer != first.proposals[0].layer
        assert not hasattr(second, "geometry")
        with pytest.raises(DomainFailure) as failure:
            module.area_evidence(
                content + b" ",
                source_sha256=digest,
                autocad_version="2027.0.1",
                target="macos-arm64",
            )
        assert failure.value.reason is FailureReason.NATIVE_AREA_EVIDENCE_CHANGED
        assert failure.value.params == {}
        assert compile.call_count == 1
