#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
sdk_root="${OBJECTARX_SDK_ROOT:-$repo_root/.local/objectarx-2027}"
acad_root="${AUTOCAD_2027_ROOT:-/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app}"
source_root="$repo_root/tools/autocad-bridge/native"
output_parent="$repo_root/.runtime/autocad-bridge/query-worker"
mkdir -p "$output_parent"
build_root="$(mktemp -d "$output_parent/build-XXXXXXXX")"
bundle="$build_root/GreenAtlasQuery.bundle"
mkdir -p "$bundle/Contents/MacOS"
cp "$source_root/Info.plist" "$bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier ru.green-atlas.native-query-worker' "$bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Set :CFBundleName Green Atlas Native Query Worker' "$bundle/Contents/Info.plist"
xcrun clang++ -std=c++17 -arch arm64 -arch x86_64 -mmacosx-version-min=14.0 -bundle -O2 \
  -DNDEBUG -D_ADESK_MAC_ -DOSX_SYSTEM -D_NATIVE_WCHAR_T_DEFINED -DUNICODE -DACDB_EXT \
  -Wno-deprecated-declarations -Wno-nonportable-include-path -Wno-extra-tokens \
  -Wno-parentheses -Wno-unused -Wno-comment -Wno-switch-enum -DDLLNAME_ACBR=AcBr.dbx \
  -include "$source_root/prefix.pch" \
  -I "$source_root" -I "$sdk_root/inc" -I "$sdk_root/utils/brep/inc" \
  -L "$acad_root/Contents/Frameworks" -F "$acad_root/Contents/Frameworks" \
  -lacfirst -lwinapi -lacdb -laccore -lgelib -lAcPal -lgelibx \
  "$acad_root/Contents/Plugins/AcGeomentObj.dbx/AcGeomentObj" \
  "$acad_root/Contents/Plugins/AcBr.dbx/AcBr" \
  "$repo_root/tools/autocad-bridge/query-worker/entry.cpp" \
  "$repo_root/tools/autocad-bridge/query-worker/package_query.cpp" \
  "$repo_root/tools/autocad-bridge/query-worker/cad_release.cpp" \
  "$source_root/session_capture_instances.cpp" \
  "$source_root/direct_query_kernel.cpp" \
  "$source_root/native_affine_query.cpp" \
  "$source_root/native_area_group.cpp" \
  "$source_root/native_area_candidates.cpp" \
  "$source_root/planar_faces.cpp" \
  "$source_root/planar_face_split.cpp" \
  "$source_root/planar_face_repairs.cpp" \
  "$source_root/planar_face_graph.cpp" \
  "$source_root/native_face_catalog.cpp" \
  "$source_root/cad_utils.cpp" \
  "$source_root/native_curve_query.cpp" \
  "$source_root/native_clearance.cpp" \
  "$source_root/native_clearance_cache.cpp" \
  "$source_root/native_clearance_targets.cpp" \
  "$source_root/native_clearance_domain.cpp" \
  "$source_root/native_query_batch.cpp" \
  "$source_root/native_query_command.cpp" \
  "$source_root/xref_instance_access.cpp" \
  "$source_root/file_io.cpp" \
  -o "$bundle/Contents/MacOS/GreenAtlasBridge"
codesign --force --sign - "$bundle"
codesign --verify --strict "$bundle"
echo "$bundle"
