#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
build_python="${GREEN_ATLAS_BUILD_PYTHON:-$repo_root/apps/api/.venv/bin/python}"
"$build_python" -c 'import PyInstaller; assert PyInstaller.__version__ == "6.22.0", "Install the pinned desktop build dependencies first"'
distribution_root="$repo_root/.runtime/autocad-bridge/desktop-runtime"
mkdir -p "$distribution_root"
staging_root="$(mktemp -d "$distribution_root/build-XXXXXXXX")"
export PYINSTALLER_CONFIG_DIR="$staging_root/cache"
export PYTHONPATH="$repo_root/apps/api"
"$build_python" -m PyInstaller --noconfirm \
  --distpath "$staging_root/dist" --workpath "$staging_root/work" \
  "$repo_root/tools/autocad-bridge/desktop/runtime.spec"
runtime="$staging_root/dist/GreenAtlasRuntime"
# A newer build host can silently bundle a Python framework that requires its
# own macOS version. The app's Info.plist alone does not make it compatible.
while IFS= read -r -d '' binary; do
  if ! file -b "$binary" | grep -q 'Mach-O'; then continue; fi
  metadata="$(xcrun vtool -show-build -arch arm64 "$binary")"
  minimum="$(printf '%s\n' "$metadata" | awk '$1 == "minos" { print $2; exit }')"
  if [[ -z "$minimum" ]]; then
    echo "Missing macOS minimum version: $binary" >&2
    exit 1
  fi
  major="${minimum%%.*}"
  minor="${minimum#*.}"
  minor="${minor%%.*}"
  if (( major > 14 || (major == 14 && minor > 0) )); then
    echo "macOS $minimum binary exceeds the supported macOS 14 minimum: $binary" >&2
    echo 'Build with an ARM Python and wheels targeting macOS 14 or earlier; changing Info.plist is not sufficient.' >&2
    exit 1
  fi
done < <(find "$runtime" -type f \( -name '*.dylib' -o -name '*.so' -o -name Python -o -name GreenAtlasRuntime \) -print0)
echo "$runtime"
echo 'Development bundle: qualify platform/runtime, signing and clean-profile installation separately.' >&2
