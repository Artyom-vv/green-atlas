#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
output_root="$repo_root/.runtime/autocad-bridge/launcher"
mkdir -p "$output_root"
stage="$(mktemp -d "$output_root/build-XXXXXXXX")"
app="$stage/Green Atlas.app"
mkdir -p "$app/Contents/MacOS"
cp "$repo_root/tools/autocad-bridge/desktop/Launcher-Info.plist" "$app/Contents/Info.plist"
if [[ -n "${GREEN_ATLAS_LAUNCHER_TEST_PACKAGE:-}" ]]; then
  /usr/libexec/PlistBuddy -c "Add :GADevelopmentPackageRoot string $GREEN_ATLAS_LAUNCHER_TEST_PACKAGE" "$app/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier ru.green-atlas.launcher.qa' "$app/Contents/Info.plist"
fi
xcrun clang++ -std=c++17 -fobjc-arc -arch arm64 -mmacosx-version-min=14.0 -O2 \
  -Wall -Wextra -Wno-unused-parameter -framework AppKit \
  "$repo_root/tools/autocad-bridge/desktop/launcher-macos.mm" -o "$app/Contents/MacOS/GreenAtlasLauncher"
codesign --force --options runtime --sign "${GREEN_ATLAS_SIGNING_IDENTITY:--}" "$app"
codesign --verify --deep --strict "$app"
echo "$app"
