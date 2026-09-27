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
echo "$staging_root/dist/GreenAtlasRuntime"
echo 'Development bundle: qualify platform/runtime, signing and clean-profile installation separately.' >&2
