#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
runtime_root="${1:?Pass the built GreenAtlasRuntime directory}"
test -f "$runtime_root/GreenAtlasRuntime"
output_root="$repo_root/.runtime/autocad-bridge/desktop-app"
mkdir -p "$output_root"
stage="$(mktemp -d "$output_root/build-XXXXXXXX")"
# Exercise the native shell's handoff contract, not just the API route tests.
# A live import may already be saved when an incompatible shell rejects /setup.
xcrun clang++ -std=c++17 -fobjc-arc -framework Foundation \
  "$repo_root/tools/autocad-bridge/desktop/test_project_route.mm" -o "$stage/test-project-route"
"$stage/test-project-route"
app="$stage/Green Atlas.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp "$repo_root/tools/autocad-bridge/desktop/Info.plist" "$app/Contents/Info.plist"
if [[ -n "${GREEN_ATLAS_DESKTOP_TEST_PROFILE:-}" ]]; then
  /usr/libexec/PlistBuddy -c "Add :GADevelopmentProfile string $GREEN_ATLAS_DESKTOP_TEST_PROFILE" "$app/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier ru.green-atlas.desktop.qa' "$app/Contents/Info.plist"
fi
ditto "$runtime_root" "$app/Contents/Resources/Runtime"
if [[ -n "${GREEN_ATLAS_LAYER_RECOGNITION_PRESETS:-}" ]]; then
  ditto "$GREEN_ATLAS_LAYER_RECOGNITION_PRESETS" "$app/Contents/Resources/LayerRecognition"
fi
cd "$repo_root/apps/web"
VITE_API_URL='' node node_modules/vite/bin/vite.js build --outDir "$app/Contents/Resources/Web"
xcrun clang++ -std=c++17 -fobjc-arc -arch arm64 -mmacosx-version-min=14.0 -O2 \
  -Wall -Wextra -Wno-unused-parameter -framework AppKit -framework WebKit \
  "$repo_root/tools/autocad-bridge/desktop/mac-main.mm" -o "$app/Contents/MacOS/GreenAtlasDesktop"
codesign --force --options runtime --sign "${GREEN_ATLAS_SIGNING_IDENTITY:--}" "$app"
codesign --verify --deep --strict "$app"
echo "$app"
echo 'Local macOS 14+ arm64 test build, not a notarized distribution.' >&2
