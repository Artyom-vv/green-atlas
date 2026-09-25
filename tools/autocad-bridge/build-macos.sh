#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
sdk_root="${OBJECTARX_SDK_ROOT:-$repo_root/.local/objectarx-2027}"
autocad_root="${AUTOCAD_2027_ROOT:-/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app}"
source_root="$repo_root/tools/autocad-bridge/native"
output_root="$repo_root/.runtime/autocad-bridge/macos"
mkdir -p "$output_root"
build_root="$(mktemp -d "$output_root/build-XXXXXXXX")"
bundle_root="$build_root/GreenAtlasBridge.bundle"
package_root="$build_root/GreenAtlasBridge.package.bundle"
frameworks="$autocad_root/Contents/Frameworks"
plugins="$autocad_root/Contents/Plugins"
test_flag=""
if [[ "${GREEN_ATLAS_GEOMETRY_SELFTEST:-0}" == 1 ]]; then
  test_flag="-DGA_GEOMETRY_SELFTEST"
fi

test -f "$sdk_root/inc/acdb.h" || {
  echo "ObjectARX SDK not found at $sdk_root" >&2
  exit 1
}
test -d "$frameworks" || {
  echo "AutoCAD 2027 not found at $autocad_root" >&2
  exit 1
}

mkdir -p "$bundle_root/Contents/MacOS"
cp "$source_root/Info.plist" "$bundle_root/Contents/Info.plist"

# AppKit and the ObjectARX WinStubs prefix define incompatible platform types.
# Keep them in separate translation units with a plain C++ boundary.
xcrun clang++ -std=c++17 -fobjc-arc -arch arm64 -arch x86_64 \
  -mmacosx-version-min=14.0 -O2 -Wno-deprecated-declarations \
  -c "$source_root/delivery_ui.mm" -o "$build_root/delivery_ui.o"
xcrun clang++ -std=c++17 -fobjc-arc -arch arm64 -arch x86_64 \
  -mmacosx-version-min=14.0 -O2 -Wno-deprecated-declarations \
  -c "$source_root/reference_search.mm" -o "$build_root/reference_search.o"

xcrun clang++ \
  -std=c++17 \
  -arch arm64 \
  -arch x86_64 \
  -mmacosx-version-min=14.0 \
  -bundle \
  -O2 \
  ${test_flag:+"$test_flag"} \
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
  "$source_root/cad_utils.cpp" \
  "$source_root/file_io.cpp" \
  "$source_root/geometry_math.cpp" \
  "$source_root/direct_query_kernel.cpp" \
  "$source_root/native_affine_query.cpp" \
  "$source_root/native_area_group.cpp" \
  "$source_root/native_area_candidates.cpp" \
  "$source_root/planar_faces.cpp" \
  "$source_root/planar_face_split.cpp" \
  "$source_root/planar_face_repairs.cpp" \
  "$source_root/planar_face_graph.cpp" \
  "$source_root/native_face_catalog.cpp" \
  "$source_root/native_curve_query.cpp" \
  "$source_root/native_clearance.cpp" \
  "$source_root/native_clearance_cache.cpp" \
  "$source_root/native_clearance_targets.cpp" \
  "$source_root/native_clearance_domain.cpp" \
  "$source_root/native_query_batch.cpp" \
  "$source_root/native_query_command.cpp" \
  "$source_root/live_query_session.cpp" \
  "$source_root/live_inventory.cpp" \
  "$source_root/live_query_transport.cpp" \
  "$source_root/xref_instance_access.cpp" \
  "$source_root/operation_control.cpp" \
  "$source_root/curve_sampling.cpp" \
  "$source_root/curve_extraction.cpp" \
  "$source_root/region_extraction.cpp" \
  "$source_root/hatch_loop_roles.cpp" \
  "$source_root/hatch_extraction.cpp" \
  "$source_root/xref_resolver.cpp" \
  "$source_root/drawing_traversal.cpp" \
  "$source_root/exact_polyline_pairs.cpp" \
  "$source_root/block_traversal.cpp" \
  "$source_root/entity_extraction.cpp" \
  "$source_root/snapshot_writer.cpp" \
  "$source_root/capture_commands.cpp" \
  "$source_root/probe_command.cpp" \
  "$source_root/mcp_transport.cpp" \
  "$source_root/geometry_selftest.cpp" \
  "$source_root/delivery_command.cpp" \
  "$source_root/delivery_recovery.cpp" \
  "$build_root/delivery_ui.o" "$build_root/reference_search.o" -framework AppKit -framework Foundation \
  -o "$bundle_root/Contents/MacOS/GreenAtlasBridge"

codesign --force --sign - "$bundle_root"
mkdir -p "$package_root/Contents/MacOS"
cp "$repo_root/tools/autocad-bridge/PackageContents.xml" \
  "$package_root/PackageContents.xml"
cp "$source_root/green_atlas_loader.lsp" \
  "$package_root/Contents/green_atlas_loader.lsp"
ditto "$bundle_root" "$package_root/Contents/MacOS/GreenAtlasBridge.bundle"
# The background worker uses the same native query sources, without editor
# menus or the editor MCP queue. It is shipped with the matching plugin build.
query_worker_bundle="$(bash "$repo_root/tools/autocad-bridge/build-query-worker-macos.sh")"
mkdir -p "$package_root/Contents/Workers"
ditto "$query_worker_bundle" "$package_root/Contents/Workers/GreenAtlasQuery.bundle"
desktop_app="${GREEN_ATLAS_DESKTOP_APP:-}"
if [[ -z "$desktop_app" ]]; then
  echo 'Set GREEN_ATLAS_DESKTOP_APP to a qualified local app before packaging.' >&2
  echo 'Native-only build; do not install as the complete product.' >&2
else
  test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$desktop_app/Contents/Info.plist")" = 'ru.green-atlas.desktop'
  if /usr/libexec/PlistBuddy -c 'Print :GADevelopmentProfile' "$desktop_app/Contents/Info.plist" >/dev/null 2>&1; then
    echo 'Development-profile app cannot be included in a user package.' >&2
    exit 1
  fi
  codesign --verify --deep --strict "$desktop_app"
  mkdir -p "$package_root/Contents/Applications"
  ditto "$desktop_app" "$package_root/Contents/Applications/Green Atlas.app"
fi
echo "$bundle_root"
echo "$package_root"
