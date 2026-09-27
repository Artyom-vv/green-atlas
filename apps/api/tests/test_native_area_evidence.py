from collections import OrderedDict
from hashlib import sha256
from unittest.mock import patch

import pytest
from test_autocad_live_import import live_area_probe, live_bytes

from app.cad_bridge.compiler import compile_live_document
from app.dxf_import import native_area_evidence as module


def test_admits_once_caches_only_small_evidence_and_still_rejects_changed_bytes(monkeypatch):
    monkeypatch.setattr(module, '_cache', OrderedDict())
    content = live_bytes(live_area_probe())
    args = dict(source_sha256=sha256(content).hexdigest(), autocad_version='2027.0.1',target='macos-arm64')
    with patch.object(module, 'compile_live_document', wraps=compile_live_document) as compile:
        first = module.area_evidence(content, **args)
        first.proposals[0].layer = 'tampered cache consumer'
        second = module.area_evidence(content, **args)
        assert compile.call_count == 1
        assert second.proposals[0].layer != first.proposals[0].layer
        assert not hasattr(second, 'geometry')
        with pytest.raises(ValueError, match='изменился'):
            module.area_evidence(content + b' ', **args)
        assert compile.call_count == 1
