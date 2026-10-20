#!/usr/bin/env bash
set -euo pipefail

# Build machine only. The resulting app needs no SDK, interpreter or checkout.
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
bridge_package="${1:?Pass the freshly built GreenAtlasBridge.package.bundle directory}"
installer_source="$repo_root/tools/autocad-bridge/installer/macos"
distribution_root="$repo_root/.runtime/autocad-bridge/distribution"
test -f "$bridge_package/PackageContents.xml" || {
  echo 'Build the native plugin with build-macos.sh first.' >&2
  exit 1
}
codesign --verify --deep --strict "$bridge_package/Contents/MacOS/GreenAtlasBridge.bundle"
desktop_app="$bridge_package/Contents/Applications/Green Atlas.app"
test -f "$desktop_app/Contents/Resources/Runtime/GreenAtlasRuntime"
test -f "$desktop_app/Contents/Resources/Web/index.html"
if /usr/libexec/PlistBuddy -c 'Print :GADevelopmentProfile' "$desktop_app/Contents/Info.plist" >/dev/null 2>&1; then
  echo 'Development-profile app cannot be distributed.' >&2
  exit 1
fi
codesign --verify --deep --strict "$desktop_app"
bridge_version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$bridge_package/Contents/MacOS/GreenAtlasBridge.bundle/Contents/Info.plist")"
desktop_version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$desktop_app/Contents/Info.plist")"
package_version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$repo_root/tools/autocad-bridge/desktop/Info.plist")"
launcher_version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$repo_root/tools/autocad-bridge/desktop/Launcher-Info.plist")"
if [[ "$desktop_version" != "$package_version" || "$launcher_version" != "$package_version" ]]; then
  echo "Desktop/launcher must match the owned package version $package_version." >&2
  exit 1
fi
build_python="${GREEN_ATLAS_BUILD_PYTHON:-$repo_root/apps/api/.venv/bin/python}"
PYTHONPATH="$repo_root/apps/api" "$build_python" - "$bridge_version" "$package_version" "$bridge_package/PackageContents.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET
from app.cad_bridge.config import PRODUCER_CAPABILITIES, ProbeCapability
from app.desktop.tickets import ArchivedTicket

version = sys.argv[1]
if ET.parse(sys.argv[3]).getroot().attrib.get("AppVersion") != sys.argv[2]:
    raise SystemExit("PackageContents.xml must match the desktop package version")
accepted = ArchivedTicket.model_fields["plugin_version"].annotation.__args__
required = {ProbeCapability.XREF_DEPENDENCIES, ProbeCapability.AREA_PROPOSALS}
if version not in accepted or not required.issubset(
    PRODUCER_CAPABILITIES.get(version, frozenset())
):
    raise SystemExit(f"Desktop API cannot accept the packaged plugin {version}")
PY
mkdir -p "$distribution_root"
staging_root="$(mktemp -d "$distribution_root/build-XXXXXXXX")"
installer_app="$staging_root/Green Atlas Installer.app"
mkdir -p "$installer_app/Contents/MacOS" "$installer_app/Contents/Resources"
cp "$installer_source/Info.plist" "$installer_app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $package_version" "$installer_app/Contents/Info.plist"
ditto "$bridge_package" "$installer_app/Contents/Resources/GreenAtlasBridge.bundle"
launcher_app="$(bash "$repo_root/tools/autocad-bridge/build-launcher-macos.sh")"
if /usr/libexec/PlistBuddy -c 'Print :GADevelopmentPackageRoot' "$launcher_app/Contents/Info.plist" >/dev/null 2>&1; then
  echo 'Development launcher cannot be distributed.' >&2
  exit 1
fi
ditto "$launcher_app" "$installer_app/Contents/Resources/Green Atlas Launcher.app"
/usr/libexec/PlistBuddy -c 'Set :LSMinimumSystemVersion 14.0' "$installer_app/Contents/Info.plist"
xcrun clang++ -std=c++17 -fobjc-arc -arch arm64 \
  -mmacosx-version-min=14.0 -O2 -Wall -Wextra -Wno-unused-parameter \
  -framework AppKit -framework Foundation \
  "$installer_source/main.mm" "$installer_source/launcher-entry.mm" "$installer_source/install-transaction.mm" "$installer_source/runtime-guard.mm" \
  "$installer_source/qualification-root.mm" -o "$installer_app/Contents/MacOS/GreenAtlasInstaller"
signing_identity="${GREEN_ATLAS_SIGNING_IDENTITY:--}"
if [[ "$signing_identity" != '-' ]]; then
  codesign --force --options runtime --timestamp --sign "$signing_identity" \
    "$installer_app/Contents/Resources/GreenAtlasBridge.bundle/Contents/MacOS/GreenAtlasBridge.bundle"
fi
# Build receipts and notices accompany the installer, not the managed program
# directories. This keeps the installer's strict disposable-payload policy.
if [[ -n "${GREEN_ATLAS_DISTRIBUTION_NOTICES:-}" ]]; then
  ditto "$GREEN_ATLAS_DISTRIBUTION_NOTICES" "$installer_app/Contents/Resources/Notices"
fi
if [[ -n "${GREEN_ATLAS_SOURCE_MANIFEST:-}" ]]; then
  final_payload="$installer_app/Contents/Resources/GreenAtlasBridge.bundle"
  final_manifest="$installer_app/Contents/Resources/distribution-manifest.json"
  "$build_python" "$repo_root/scripts/desktop/package_preflight.py" build \
    --package "$final_payload" --source-root "${GREEN_ATLAS_SOURCE_ROOT:-$repo_root}" \
    --source-manifest "$GREEN_ATLAS_SOURCE_MANIFEST" --output "$final_manifest"
  "$build_python" "$repo_root/scripts/desktop/package_preflight.py" check \
    --package "$final_payload" --manifest "$final_manifest"
fi
codesign --force --options runtime --sign "$signing_identity" "$installer_app"
codesign --verify --deep --strict "$installer_app"
"$installer_app/Contents/MacOS/GreenAtlasInstaller" --self-test
ditto -c -k --sequesterRsrc --keepParent "$installer_app" "$staging_root/GreenAtlas-AutoCAD-Mac-$package_version.zip"
echo "$installer_app"
echo "$staging_root/GreenAtlas-AutoCAD-Mac-$package_version.zip"
if [[ "$signing_identity" == '-' ]]; then
  echo 'LOCAL TEST BUILD: ad-hoc signed, not notarized for public distribution.' >&2
fi
