"""Transport checks do not claim Windows UI acceptance."""

import base64
import hashlib
import json

import pytest

from scripts.desktop import windows_ci_evidence as evidence

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=="
)


def test_original_frame_bytes_and_only_allowlisted_receipt_leave_the_output(tmp_path):
    directory = tmp_path / "wizard-smoke/user-checks"
    directory.mkdir(parents=True)
    (directory / "07-launched-app.png").write_bytes(PNG)
    secret = "private_" + "x" * 50
    (directory / "install-ci-receipt.json").write_text(
        json.dumps(
            {
                "passed": False,
                "error": "Opaque diagnostic " + secret,
                "session_token": secret,
                "environment": {"GH_TOKEN": secret},
                "wizard_browse_buttons_tested": False,
                "wizard_browse_accept_fields": ["autocad_root", "autocad_template", "blender", "botaniq_root", "pbr_root"],
                "wizard_browse_cancel_preserved_fields": ["autocad_root", "autocad_template", "blender", "botaniq_root", "pbr_root"],
                "component_ui": {
                    "passed": True,
                    "native_picker_accept_fields": ["autocad_root", "autocad_template"],
                    "native_picker_cancel_preserved_fields": ["autocad_root", "autocad_template"],
                    "session_token": secret,
                },
                "screenshots": [
                    {
                        "file": "07-launched-app.png",
                        "sha256": hashlib.sha256(PNG).hexdigest(),
                        "private": secret,
                    }
                ],
            }
        )
    )
    lines = []
    evidence.emit(tmp_path, lines.append)
    assert secret not in "\n".join(lines)
    record = json.loads(lines[0].removeprefix("GA_CI_RECEIPT "))
    assert record["receipt"]["passed"] is False
    assert record["receipt"]["wizard_browse_buttons_tested"] is False
    assert record["receipt"]["wizard_browse_accept_fields"] == ["autocad_root", "autocad_template", "blender", "botaniq_root", "pbr_root"]
    assert record["receipt"]["wizard_browse_cancel_preserved_fields"] == ["autocad_root", "autocad_template", "blender", "botaniq_root", "pbr_root"]
    component = record["receipt"]["component_ui"]
    assert component["native_picker_accept_fields"] == ["autocad_root", "autocad_template"]
    assert component["native_picker_cancel_preserved_fields"] == ["autocad_root", "autocad_template"]
    metadata = json.loads(
        next(
            line.removeprefix("GA_CI_IMAGE_BEGIN ")
            for line in lines
            if line.startswith("GA_CI_IMAGE_BEGIN ")
        )
    )
    encoded = "".join(
        line.removeprefix("GA_CI_IMAGE_DATA ")
        for line in lines
        if line.startswith("GA_CI_IMAGE_DATA ")
    )
    assert base64.b64decode(encoded) == PNG
    assert metadata["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert (metadata["width"], metadata["height"]) == (1, 1)


def test_link_cannot_export_an_external_file(tmp_path):
    directory = tmp_path / "wizard-smoke/user-checks"
    directory.mkdir(parents=True)
    external = tmp_path / "external.png"
    external.write_bytes(PNG)
    (directory / "linked.png").symlink_to(external)
    with pytest.raises(ValueError, match="link|reparse"):
        evidence.emit(tmp_path, lambda _: None)


def test_image_budget_is_enforced_before_export(tmp_path, monkeypatch):
    directory = tmp_path / "wizard-smoke/user-checks"
    directory.mkdir(parents=True)
    (directory / "oversized.png").write_bytes(PNG)
    monkeypatch.setattr(evidence, "MAX_IMAGE_BYTES", len(PNG) - 1)
    with pytest.raises(ValueError, match="budget"):
        evidence.emit(tmp_path, lambda _: None)


def test_component_scenario_frames_remain_bounded(tmp_path):
    assert evidence.MAX_IMAGE_COUNT >= 36
    directory = tmp_path / "wizard-smoke/user-checks"
    directory.mkdir(parents=True)
    for index in range(evidence.MAX_IMAGE_COUNT):
        (directory / f"{index:02}.png").write_bytes(PNG)
    evidence.emit(tmp_path, lambda _: None)
    (directory / "overflow.png").write_bytes(PNG)
    with pytest.raises(ValueError, match="budget"):
        evidence.emit(tmp_path, lambda _: None)
