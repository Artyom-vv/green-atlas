"""Current GAOPEN identity contract, not native extraction acceptance.

The fixture uses versions read from the actual native producer/GAOPEN source,
not a self-consistent pair invented by the test.
"""

import json
import re
from hashlib import sha256
from pathlib import Path

from test_cad_bridge_compiler import valid_probe

from app.cad_bridge.compiler import compile_live_document
from app.desktop.tickets import LiveTicket


def test_actual_native_producer_versions_admit_the_transfer():
    native = Path(__file__).resolve().parents[3] / "tools/autocad-bridge/native"
    version_match = re.search(
        r'kPluginVersion\s*=\s*"([^"]+)"', (native / "bridge_config.h").read_text()
    )
    ticket_match = re.search(
        r"writeLiveTicket\(\s*directory,\s*([^,]+),",
        (native / "delivery_command.cpp").read_text(),
    )
    if version_match is None or ticket_match is None:
        raise ValueError("Producer API changed; update the contract probe explicitly")
    version = version_match[1]
    token = ticket_match[1].strip()
    if token in {"kPluginVersion", "ga::bridge::kPluginVersion"}:
        ticket_version = version
    elif token.startswith('"') and token.endswith('"'):
        ticket_version = json.loads(token)
    else:
        raise ValueError("Unknown version source; inspect producer contract")
    probe = valid_probe()
    probe["plugin_version"] = version
    probe["capture_mode"] = "live_document"
    probe["source"].update(database_modified_flags=32, live_database_matches_disk=False)
    probe["area_proposals"] = []
    probe["area_proposal_rejections"] = []
    probe["summary"].update(paths=0, points=0, area_proposals=0,
                            area_proposal_candidates=0, area_proposal_rejected=0)
    payload = json.dumps(probe).encode()
    ticket = LiveTicket.model_validate({
        "schema": "green-atlas.transfer/2", "plugin_version": ticket_version,
        "source_name": "Street.dxf",
        "manifest": {
            "entry": "Drawing.autocad.json",
            "files": [{"name": "Drawing.autocad.json", "kind": "live_capture",
                       "bytes": len(payload), "sha256": sha256(payload).hexdigest()}],
        },
        "producer": {"autocad_version": "2027.0.1", "target": "macos-arm64"},
    })
    snapshot = compile_live_document(payload, **ticket.producer.model_dump())
    assert snapshot.extraction.plugin_version == ticket.plugin_version == version
    assert snapshot.source.sha256 == ticket.manifest.files[0].sha256
    assert snapshot.source.live_capture.database_modified_flags == 32
    assert snapshot.geometry
