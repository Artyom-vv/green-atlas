#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
sdk_root="${OBJECTARX_SDK_ROOT:-$repo_root/.local/objectarx-2027}"
autocad_root="${AUTOCAD_2027_ROOT:-/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app}"
source_root="$repo_root/tools/autocad-bridge/native"
build_root="$repo_root/.runtime/autocad-bridge/macos"
bundle_root="$build_root/GreenAtlasBridge.bundle"
package_root="$build_root/GreenAtlasBridge.package.bundle"
frameworks="$autocad_root/Contents/Frameworks"
plugins="$autocad_root/Contents/Plugins"

test -f "$sdk_root/inc/acdb.h" || {
  echo "ObjectARX SDK not found at $sdk_root" >&2
  exit 1
}
test -d "$frameworks" || {
  echo "AutoCAD 2027 not found at $autocad_root" >&2
  exit 1
}

rm -rf "$bundle_root"
mkdir -p "$bundle_root/Contents/MacOS"
cp "$source_root/Info.plist" "$bundle_root/Contents/Info.plist"

xcrun clang++ \
  -std=c++17 \
  -arch arm64 \
  -arch x86_64 \
  -mmacosx-version-min=14.0 \
  -bundle \
  -O2 \
  -DNDEBUG -D_ADESK_MAC_ -DOSX_SYSTEM -D_NATIVE_WCHAR_T_DEFINED -DUNICODE -DACDB_EXT \
  -Wno-deprecated-declarations \
  -Wno-nonportable-include-path \
  -Wno-extra-tokens \
  -Wno-parentheses \
  -Wno-unused \
  -Wno-comment \
  -Wno-switch-enum \
  -DDLLNAME_ACBR=AcBr.dbx \
  -include "$source_root/prefix.pch" \
  -I "$sdk_root/inc" \
  -I "$sdk_root/utils/brep/inc" \
  -L "$frameworks" \
  -F "$frameworks" \
  -lacfirst -lwinapi -lacdb -laccore -lgelib -lAcPal \
  -lgelibx \
  "$plugins/AcGeomentObj.dbx/AcGeomentObj" \
  "$plugins/AcBr.dbx/AcBr" \
  "$source_root/green_atlas_bridge.cpp" \
  -o "$bundle_root/Contents/MacOS/GreenAtlasBridge"

codesign --force --sign - "$bundle_root"
rm -rf "$package_root"
mkdir -p "$package_root/Contents/MacOS"
cp "$repo_root/tools/autocad-bridge/PackageContents.xml" \
  "$package_root/PackageContents.xml"
cp "$source_root/green_atlas_loader.lsp" \
  "$package_root/Contents/green_atlas_loader.lsp"
ditto "$bundle_root" "$package_root/Contents/MacOS/GreenAtlasBridge.bundle"
echo "$bundle_root"
echo "$package_root"
