#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"
sdk_root="${OBJECTARX_SDK_ROOT:-$repo_root/.local/objectarx-2027}"
acad_root="${AUTOCAD_2027_ROOT:-/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app}"
output_parent="$repo_root/.runtime/autocad-bridge/diagnostics"
mkdir -p "$output_parent"
variant="${1:-containment}"
diagnostic_arch="${GREEN_ATLAS_DIAGNOSTIC_ARCH:-arm64}"
if [[ "$diagnostic_arch" != arm64 && "$diagnostic_arch" != x86_64 ]]; then
  echo 'GREEN_ATLAS_DIAGNOSTIC_ARCH must be arm64 or x86_64' >&2
  exit 2
fi
extra_sources=()
extra_flags=()
primary_source="$repo_root/tools/autocad-bridge/diagnostics/containment_probe.cpp"
case "$variant" in
  clipped-group)
    bundle_name=GreenAtlasClippedGroup
    primary_source="$repo_root/tools/autocad-bridge/diagnostics/native_clipped_group_probe.cpp"
    extra_sources=("$repo_root/tools/autocad-bridge/native/direct_query_kernel.cpp"
                   "$repo_root/tools/autocad-bridge/native/native_affine_query.cpp"
                   "$repo_root/tools/autocad-bridge/native/native_area_group.cpp"
                   "$repo_root/tools/autocad-bridge/native/xref_instance_access.cpp") ;;
  xref)
    bundle_name=GreenAtlasXrefLedger
    primary_source="$repo_root/tools/autocad-bridge/diagnostics/xref_ledger.cpp"
    extra_sources=("$repo_root/tools/autocad-bridge/diagnostics/xref_line_controls.cpp"
                   "$repo_root/tools/autocad-bridge/diagnostics/xref_path_probe.cpp"
                   "$repo_root/tools/autocad-bridge/diagnostics/xref_area_controls.cpp"
                   "$repo_root/tools/autocad-bridge/diagnostics/xref_subentity_controls.cpp"
                   "$repo_root/tools/autocad-bridge/diagnostics/xref_affine_controls.cpp"
                   "$repo_root/tools/autocad-bridge/native/native_affine_query.cpp"
                   "$repo_root/tools/autocad-bridge/native/native_curve_query.cpp"
                   "$repo_root/tools/autocad-bridge/native/native_area_candidates.cpp"
                   "$repo_root/tools/autocad-bridge/native/native_area_group.cpp"
                   "$repo_root/tools/autocad-bridge/native/xref_instance_access.cpp"
                   "$repo_root/tools/autocad-bridge/diagnostics/xref_window_inventory.cpp"
                   "$repo_root/tools/autocad-bridge/diagnostics/xref_window_queries.cpp"
                   "$repo_root/tools/autocad-bridge/native/direct_query_kernel.cpp") ;;
  xref-zone)
    bundle_name=GreenAtlasXrefZone
    primary_source="$repo_root/tools/autocad-bridge/diagnostics/native_zone_probe.cpp"
    extra_flags=(-DGA_XREF_ZONE)
    extra_sources=("$repo_root/tools/autocad-bridge/native/direct_query_kernel.cpp"
                   "$repo_root/tools/autocad-bridge/diagnostics/native_zone_queries.cpp"
                   "$repo_root/tools/autocad-bridge/native/xref_instance_access.cpp") ;;
  zone)
    bundle_name=GreenAtlasNativeZone
    primary_source="$repo_root/tools/autocad-bridge/diagnostics/native_zone_probe.cpp"
    extra_sources=("$repo_root/tools/autocad-bridge/native/direct_query_kernel.cpp"
                   "$repo_root/tools/autocad-bridge/diagnostics/native_zone_queries.cpp") ;;
  direct)
    bundle_name=GreenAtlasDirectQueries
    primary_source="$repo_root/tools/autocad-bridge/diagnostics/direct_query_probe.cpp"
    extra_sources=("$repo_root/tools/autocad-bridge/native/direct_query_kernel.cpp") ;;
  containment) bundle_name=GreenAtlasContainment ;;
  closure)
    bundle_name=GreenAtlasClosure
    extra_flags=(-DGA_CLOSURE_PROBE -DGA_TOPOLOGY_PROBE -DGA_PRODUCTION_PROJECTION_PROBE)
    extra_sources=(
      "$repo_root/tools/autocad-bridge/diagnostics/topology_probe.cpp"
      "$repo_root/tools/autocad-bridge/native/region_extraction.cpp"
      "$repo_root/tools/autocad-bridge/native/curve_sampling.cpp"
      "$repo_root/tools/autocad-bridge/native/geometry_math.cpp"
      "$repo_root/tools/autocad-bridge/native/cad_utils.cpp"
      "$repo_root/tools/autocad-bridge/native/operation_control.cpp") ;;
  hatch)
    bundle_name=GreenAtlasHatch
    extra_flags=(-DGA_HATCH_PROBE) ;;
  topology)
    bundle_name=GreenAtlasTopology
    extra_flags=(-DGA_TOPOLOGY_PROBE)
    extra_sources=("$repo_root/tools/autocad-bridge/diagnostics/topology_probe.cpp") ;;
  seams)
    bundle_name=GreenAtlasSeams
    extra_flags=(-DGA_TOPOLOGY_PROBE -DGA_SEAM_PROBE)
    extra_sources=("$repo_root/tools/autocad-bridge/diagnostics/topology_probe.cpp"
                   "$repo_root/tools/autocad-bridge/diagnostics/edge_probe.cpp") ;;
  distance)
    bundle_name=GreenAtlasDistance
    extra_flags=(-DGA_DISTANCE_PROBE)
    extra_sources=("$repo_root/tools/autocad-bridge/diagnostics/distance_probe.cpp") ;;
  *) echo "Usage: $0 [containment|closure|hatch|topology|seams|distance|direct|zone|xref|xref-zone]" >&2; exit 2 ;;
esac
build_root="$(mktemp -d "$output_parent/$variant-XXXXXXXX")"
bundle="$build_root/$bundle_name.bundle"
mkdir -p "$bundle/Contents/MacOS"
cp "$repo_root/tools/autocad-bridge/native/Info.plist" "$bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier ru.green-atlas.native-$variant-experiment" "$bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Set :CFBundleName Green Atlas Native Containment Experiment' "$bundle/Contents/Info.plist"
xcrun clang++ -std=c++17 -arch "$diagnostic_arch" -mmacosx-version-min=14.0 -bundle -O2 \
  -DNDEBUG -D_ADESK_MAC_ -DOSX_SYSTEM -D_NATIVE_WCHAR_T_DEFINED -DUNICODE -DACDB_EXT \
  -Wno-deprecated-declarations -Wno-nonportable-include-path -Wno-extra-tokens \
  -Wno-parentheses -Wno-unused -Wno-comment -Wno-switch-enum -DDLLNAME_ACBR=AcBr.dbx \
  -include "$repo_root/tools/autocad-bridge/native/prefix.pch" \
  -I "$sdk_root/inc" -I "$sdk_root/utils/brep/inc" \
  -I "$repo_root/tools/autocad-bridge/native" \
  -L "$acad_root/Contents/Frameworks" -F "$acad_root/Contents/Frameworks" \
  -lacfirst -lwinapi -lacdb -laccore -lgelib -lAcPal -lgelibx \
  "$acad_root/Contents/Plugins/AcGeomentObj.dbx/AcGeomentObj" \
  "$acad_root/Contents/Plugins/AcBr.dbx/AcBr" \
  "$primary_source" \
  ${extra_flags[@]+"${extra_flags[@]}"} ${extra_sources[@]+"${extra_sources[@]}"} \
  "$repo_root/tools/autocad-bridge/native/file_io.cpp" \
  -o "$bundle/Contents/MacOS/GreenAtlasBridge"
codesign --force --sign - "$bundle"
codesign --verify --strict "$bundle"
echo "$bundle"
