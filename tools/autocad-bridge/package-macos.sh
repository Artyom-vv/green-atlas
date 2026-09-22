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
mkdir -p "$distribution_root"
staging_root="$(mktemp -d "$distribution_root/build-XXXXXXXX")"
installer_app="$staging_root/Green Atlas Installer.app"
mkdir -p "$installer_app/Contents/MacOS" "$installer_app/Contents/Resources"
cp "$installer_source/Info.plist" "$installer_app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $bridge_version" "$installer_app/Contents/Info.plist"
ditto "$bridge_package" "$installer_app/Contents/Resources/GreenAtlasBridge.bundle"
/usr/libexec/PlistBuddy -c 'Set :LSMinimumSystemVersion 26.0' "$installer_app/Contents/Info.plist"
xcrun clang++ -std=c++17 -fobjc-arc -arch arm64 \
  -mmacosx-version-min=26.0 -O2 -Wall -Wextra -Wno-unused-parameter \
  -framework AppKit -framework Foundation \
  "$installer_source/main.mm" -o "$installer_app/Contents/MacOS/GreenAtlasInstaller"
signing_identity="${GREEN_ATLAS_SIGNING_IDENTITY:--}"
if [[ "$signing_identity" != '-' ]]; then
  codesign --force --options runtime --timestamp --sign "$signing_identity" \
    "$installer_app/Contents/Resources/GreenAtlasBridge.bundle/Contents/MacOS/GreenAtlasBridge.bundle"
fi
codesign --force --options runtime --sign "$signing_identity" "$installer_app"
codesign --verify --deep --strict "$installer_app"
"$installer_app/Contents/MacOS/GreenAtlasInstaller" --self-test
ditto -c -k --sequesterRsrc --keepParent "$installer_app" "$staging_root/GreenAtlas-AutoCAD-Mac-$bridge_version.zip"
echo "$installer_app"
echo "$staging_root/GreenAtlas-AutoCAD-Mac-$bridge_version.zip"
if [[ "$signing_identity" == '-' ]]; then
  echo 'LOCAL TEST BUILD: ad-hoc signed, not notarized for public distribution.' >&2
fi
