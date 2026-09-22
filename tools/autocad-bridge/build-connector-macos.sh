#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
source_root="$repo_root/tools/autocad-bridge/connector/macos"
output_root="${1:-$repo_root/.runtime/autocad-bridge/connector}"
app="$output_root/Green Atlas Connect.app"
mkdir -p "$app/Contents/MacOS"
cp "$source_root/Info.plist" "$app/Contents/Info.plist"
xcrun clang++ -std=c++17 -fobjc-arc -arch arm64 -arch x86_64 \
  -mmacosx-version-min=14.0 -O2 -Wall -Wextra -Wno-unused-parameter -Wno-deprecated-declarations \
  -framework AppKit -framework Foundation -framework Security \
  "$source_root/Transfer.mm" "$source_root/BrowserActions.mm" "$source_root/main.mm" \
  -o "$app/Contents/MacOS/GreenAtlasConnect"
codesign --force --options runtime --sign "${GREEN_ATLAS_SIGNING_IDENTITY:--}" "$app"
codesign --verify --deep --strict "$app"
"$app/Contents/MacOS/GreenAtlasConnect" --self-test
"$app/Contents/MacOS/GreenAtlasConnect" --ui-self-test
echo "$app"
