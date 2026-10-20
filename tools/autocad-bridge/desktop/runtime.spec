# Сборка из квалифицированного окружения API; каталог сохраняет native dylib.
from pathlib import Path
import json
import os
from urllib.parse import urlsplit
from PyInstaller.config import CONF
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

repo = Path(SPECPATH).resolve().parents[2]
# The temporary release default is injected by the release owner, never read
# implicitly from a developer's profile or stored in versioned sources.
service_data = []
default_settings = os.environ.get("GREEN_ATLAS_BUNDLED_DADATA_SETTINGS")
if default_settings:
    try:
        source = Path(default_settings).expanduser().resolve(strict=True)
        if not source.is_file() or source.stat().st_size > 8192:
            raise ValueError("invalid settings file")
        payload = json.loads(source.read_text(encoding="utf-8"))
        key = payload.get("api_key") if isinstance(payload, dict) else None
        if not isinstance(key, str) or not 1 <= len(key.strip()) <= 512:
            raise ValueError("invalid key")
        key = key.strip()
        if not all(33 <= ord(char) <= 126 for char in key):
            raise ValueError("invalid key")
        private_data = Path(CONF["workpath"]) / "private-services"
        private_data.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = private_data / "dadata.json"
        destination.write_text(json.dumps({"api_key": key}), encoding="utf-8")
        destination.chmod(0o600)
        service_data.append((str(destination), "Services"))
    except (OSError, ValueError):
        raise SystemExit("Invalid private DaData release settings; value omitted") from None
# This is a separate, revocable release credential, not the owner's relay token
# or an OpenAI API key. It is included only by an explicit release build input.
release_relay = os.environ.get("GREEN_ATLAS_BUNDLED_RELAY_SETTINGS")
if release_relay:
    try:
        source = Path(release_relay).expanduser().resolve(strict=True)
        if not source.is_file() or source.stat().st_size > 8192:
            raise ValueError("invalid settings file")
        payload = json.loads(source.read_text(encoding="utf-8"))
        url = payload.get("url") if isinstance(payload, dict) else None
        token = payload.get("token") if isinstance(payload, dict) else None
        if (
            not isinstance(url, str)
            or not 1 <= len(url) <= 2048
            or any(not 33 <= ord(char) <= 126 for char in url)
            or not isinstance(token, str)
            or not 32 <= len(token) <= 512
            or any(not 33 <= ord(char) <= 126 for char in token)
        ):
            raise ValueError("invalid relay settings")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/api/luna/recognize"
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError("invalid relay settings")
        private_data = Path(CONF["workpath"]) / "private-services"
        private_data.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = private_data / "relay.json"
        destination.write_text(json.dumps({"url": url, "token": token}), encoding="utf-8")
        destination.chmod(0o600)
        service_data.append((str(destination), "Services"))
    except (OSError, ValueError):
        raise SystemExit("Invalid private relay release settings; value omitted") from None
a = Analysis(
    [str(Path(SPECPATH) / "entry.py")],
    pathex=[str(repo / "apps/api")],
    hiddenimports=collect_submodules("app"),
    datas=(
        collect_data_files("ezdxf")
        + collect_data_files("app.cad_bridge", includes=["*.json"])
        + collect_data_files("app.dxf_import.layers", includes=["*.json"])
        + collect_data_files("app.regulations", includes=["*.json"])
        + collect_data_files("app.species", includes=["data/*.json"])
        + collect_data_files("app.shared", includes=["*.json"])
        + collect_data_files("app.rendering", includes=["blender_worker.py", "render_style.py", "lawn_instances.py", "lawn_scatter.py", "native_style.json", "preview_asset_worker.py"], include_py_files=True)
        + service_data
    ),
    excludes=["tkinter", "pytest", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="GreenAtlasRuntime",
          console=True, target_arch=None, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, name="GreenAtlasRuntime")
