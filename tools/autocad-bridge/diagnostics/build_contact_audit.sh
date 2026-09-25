#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"
sdk_root="$repo_root/.local/objectarx-2027"
acad_root="/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app"
source_root="$repo_root/tools/autocad-bridge/native"
mode="${1:-contact}"
case "$mode" in
  contact) probe=native_contact_audit.cpp ;;
  face) probe=native_face_audit.cpp ;;
  assembly) probe=native_face_pipeline_test.cpp ;;
  clearance) probe=native_clearance_test.cpp ;;
  *) exit 2 ;;
esac
build_root="$(mktemp -d "$repo_root/.runtime/autocad-bridge/diagnostics/contact-XXXXXXXX")"
bundle="$build_root/ContactAudit.dbx"
mkdir -p "$bundle/Contents/MacOS"
cp "$source_root/Info.plist" "$bundle/Contents/Info.plist"
xcrun clang++ -std=c++17 -arch x86_64 -mmacosx-version-min=14.0 -bundle -O2 \
  -DNDEBUG -D_ADESK_MAC_ -DOSX_SYSTEM -D_NATIVE_WCHAR_T_DEFINED -DUNICODE -DACDB_EXT \
  -Wno-deprecated-declarations -Wno-nonportable-include-path -Wno-extra-tokens \
  -Wno-parentheses -Wno-unused -Wno-comment -Wno-switch-enum -DDLLNAME_ACBR=AcBr.dbx \
  -include "$source_root/prefix.pch" \
  -I "$source_root" -I "$sdk_root/inc" -I "$sdk_root/utils/brep/inc" \
  -L "$acad_root/Contents/Frameworks" -F "$acad_root/Contents/Frameworks" \
  -lacfirst -lwinapi -lacdb -laccore -lgelib -lAcPal -lgelibx \
  "$acad_root/Contents/Plugins/AcGeomentObj.dbx/AcGeomentObj" \
  "$acad_root/Contents/Plugins/AcBr.dbx/AcBr" \
  "$repo_root/tools/autocad-bridge/diagnostics/$probe" \
  "$source_root/native_area_group.cpp" "$source_root/native_affine_query.cpp" \
  "$source_root/native_clearance.cpp" "$source_root/native_curve_query.cpp" \
  "$source_root/native_clearance_cache.cpp" \
  "$source_root/native_clearance_targets.cpp" "$source_root/native_clearance_domain.cpp" \
  "$source_root/native_face_catalog.cpp" "$source_root/cad_utils.cpp" \
  "$source_root/planar_faces.cpp" "$source_root/planar_face_split.cpp" \
  "$source_root/planar_face_repairs.cpp" "$source_root/planar_face_graph.cpp" \
  "$source_root/direct_query_kernel.cpp" \
  "$source_root/xref_instance_access.cpp" "$source_root/file_io.cpp" \
  -o "$bundle/Contents/MacOS/GreenAtlasBridge"
codesign --force --sign - "$bundle"
codesign --verify --strict "$bundle"
echo "$bundle"
