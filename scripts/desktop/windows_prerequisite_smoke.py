"""Отдельная CI-проверка обновления системного Microsoft WebView2, до обычного Setup."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]


def read_object(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400 or not path.is_file():
        raise ValueError("CI вход не является обычным файлом")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise TypeError("CI вход должен быть JSON object")
    return value


def verify_setup_binding(product: dict[str, Any], setup: dict[str, Any], manifest_source_sha: str,
                         distribution_sha: str, wizard: dict[str, Any] | None = None) -> None:
    expected_setup_sha = product["sha256"]
    if product.get("native_wizard") is True:
        if (wizard is None or wizard.get("schema") != "green-atlas.windows-native-wizard-build/1"
                or wizard.get("sha256") != product["sha256"]
                or wizard.get("backend_sha256") != setup["sha256"]
                or product.get("backend_sha256") != setup["sha256"]
                or wizard.get("source_manifest_sha256") != product["source_manifest_sha256"]
                or wizard.get("distribution_manifest_sha256") != distribution_sha):
            raise ValueError("Native мастер не связан с точным backend и payload")
        expected_setup_sha = product["backend_sha256"]
    if (expected_setup_sha != setup["sha256"]
            or product["source_manifest_sha256"] != setup["source_manifest_sha256"]
            or setup["distribution_manifest_sha256"] != distribution_sha
            or setup["source_manifest_sha256"] != manifest_source_sha):
        raise ValueError("Квитанция Setup не соответствует sealed manifest")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Допустим только одноразовый настоящий Windows CI")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("Этот этап требует административный CI token; Setup запускается отдельно")

    from app.distribution.windows_install import plain_directory
    from app.distribution.windows_prerequisite_elevation import (
        run_elevated_webview_installer,
    )
    from app.distribution.windows_prerequisites import (
        supported_runtime_version,
        webview_runtime_version,
    )

    root = args.product.absolute()
    if not root.is_relative_to(REPOSITORY / ".runtime"):
        parser.error("Product должен находиться внутри .runtime checkout")
    plain_directory(root)
    evidence = plain_directory(root / "evidence")
    installer = root / "tools/MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
    inputs = read_object(REPOSITORY / "tools/autocad-bridge/windows-product-inputs.json")
    expected = inputs["webview_runtime"]["sha256"]
    manifest = read_object(evidence / "distribution-manifest.json")
    setup = read_object(evidence / "setup-build-receipt.json")
    product = read_object(root / "deliverable/product-build-receipt.json")
    verification_commit = os.environ.get("GITHUB_SHA")
    code_commit = product["git_commit"]
    if code_commit != verification_commit:
        recheck = read_object(evidence / "candidate-recheck-source.json")
        if recheck.get("candidate_code_git") != code_commit or recheck.get("verification_git") != verification_commit:
            raise ValueError("Повторная проверка должна явно связывать код кандидата и новый CI harness")
    distribution_sha = hashlib.sha256((evidence / "distribution-manifest.json").read_bytes()).hexdigest()
    wizard = read_object(evidence / "wizard-build-receipt.json") if product.get("native_wizard") is True else None
    verify_setup_binding(product, setup, manifest["source"]["manifest_sha256"], distribution_sha, wizard)
    entries = [row for row in manifest["entries"] if row["path"] == "Contents/Windows/Prerequisites/MicrosoftEdgeWebView2RuntimeInstallerX64.exe"]
    if len(entries) != 1 or entries[0]["kind"] != "file" or entries[0]["sha256"] != expected:
        raise ValueError("Microsoft prerequisite не связан с точными байтами sealed payload")
    plain_directory(installer.parent)
    info = installer.lstat()
    if (installer.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400
            or not installer.is_file() or info.st_size != entries[0]["size_bytes"]):
        raise ValueError("Microsoft prerequisite имеет другой тип или размер")
    with installer.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise ValueError("Microsoft prerequisite отличается от sealed payload")
    receipt: dict[str, Any] = {
        "schema": "green-atlas.windows-machine-prerequisite-ci/1", "passed": False,
        "admin_ci_token": True, "setup_elevated": False, "interactive_uac_tested": False,
        "cold_per_user_webview_install_tested": False, "product_qualified": False,
        "installer_sha256": expected, "sealed_payload_bytes_verified": True,
        "git_commit": code_commit, "verification_commit": verification_commit,
        "installer_executed": False,
        "setup_sha256": setup["sha256"], "source_manifest_sha256": setup["source_manifest_sha256"],
        "distribution_manifest_sha256": distribution_sha,
    }
    try:
        receipt["webview2_before"] = webview_runtime_version()
        if not supported_runtime_version(receipt["webview2_before"]):
            receipt["installer_executed"] = True
            code = run_elevated_webview_installer(installer, expected_sha256=expected)
            receipt["installer_exit_code"] = code
            if code not in (0, 3010):
                raise RuntimeError("Microsoft prerequisite завершился с кодом " + str(code))
        receipt["webview2_after"] = webview_runtime_version()
        if not supported_runtime_version(receipt["webview2_after"]):
            raise RuntimeError("Системный WebView2 не обновился до совместимой версии")
        receipt["passed"] = True
    except (OSError, RuntimeError, ValueError) as error:
        receipt["error"] = str(error)
    destination = evidence / "machine-webview2-upgrade.json"
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(receipt))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
