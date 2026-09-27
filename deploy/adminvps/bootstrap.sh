#!/usr/bin/env bash
set -euo pipefail
release_name=${1:?Pass a release directory name}
[[ "$release_name" =~ ^[a-zA-Z0-9._-]+$ ]] || exit 2
release_dir="/opt/green-atlas/releases/$release_name"
test -f "$release_dir/api/pyproject.toml"
test -f "$release_dir/api/.python-version"
test -f "$release_dir/api/scripts/check_runtime.py"
id green-atlas >/dev/null 2>&1 || useradd --system --home-dir /var/lib/green-atlas --shell /usr/sbin/nologin green-atlas
install -d -o green-atlas -g green-atlas -m 750 /var/lib/green-atlas
install -d -m 755 /etc/green-atlas /var/www/green-atlas-acme
if ! test -f /etc/green-atlas/runtime.env; then
    printf '%s\n' 'GREEN_ATLAS_DB_PATH=/var/lib/green-atlas/green-atlas.sqlite3' 'GREEN_ATLAS_LOCAL_MODEL=' 'PYTHONDONTWRITEBYTECODE=1' > /etc/green-atlas/runtime.env
    chmod 600 /etc/green-atlas/runtime.env
fi
test -x /opt/green-atlas/deploy-venv/bin/python || python3 -m venv /opt/green-atlas/deploy-venv
test -x /opt/green-atlas/deploy-venv/bin/uv || /opt/green-atlas/deploy-venv/bin/pip install uv
UV_PYTHON_INSTALL_DIR=/opt/green-atlas/python UV_PROJECT_ENVIRONMENT=/opt/green-atlas/venv /opt/green-atlas/deploy-venv/bin/uv sync --locked --no-dev --python "$(cat "$release_dir/api/.python-version")" --project "$release_dir/api"
/opt/green-atlas/venv/bin/python "$release_dir/api/scripts/check_runtime.py"
ln -sfn "$release_dir" /opt/green-atlas/current
cp "$release_dir/deploy/green-atlas-api.service" /etc/systemd/system/green-atlas-api.service
systemctl daemon-reload
systemctl enable green-atlas-api
systemctl restart green-atlas-api
for attempt in $(seq 1 30); do
    if curl --fail --silent http://127.0.0.1:8000/api/health; then exit 0; fi
    sleep 1
done
journalctl -u green-atlas-api -n 30 --no-pager
exit 1
