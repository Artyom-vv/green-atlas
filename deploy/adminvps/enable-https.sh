#!/usr/bin/env bash
set -euo pipefail
domain=green-atlas.130-49-146-234.sslip.io
test -f /opt/green-atlas/current/deploy/nginx.conf
python3 - <<'PY'
import json, secrets, subprocess
from pathlib import Path
access=Path('/root/green-atlas-access.json')
if not access.exists():
    data={'url':'https://green-atlas.130-49-146-234.sslip.io', 'username':'beta', 'password':secrets.token_urlsafe(18)}
    access.write_text(json.dumps(data,indent=2))
    access.chmod(0o600)
data=json.loads(access.read_text())
subprocess.run(['htpasswd','-ciB','/etc/nginx/green-atlas.htpasswd',data['username']], input=data['password']+'\n',text=True,check=True,stdout=subprocess.DEVNULL)
Path('/etc/nginx/green-atlas.htpasswd').chmod(0o640)
subprocess.run(['chown','root:www-data','/etc/nginx/green-atlas.htpasswd'],check=True)
PY
if test -L /etc/nginx/sites-enabled/default; then unlink /etc/nginx/sites-enabled/default; fi
cat > /etc/nginx/sites-available/green-atlas <<'NGINX'
server {
    listen 80 default_server;
    server_name green-atlas.130-49-146-234.sslip.io;
    location /.well-known/acme-challenge/ { root /var/www/green-atlas-acme; }
    location / { return 503; }
}
NGINX
ln -sfn /etc/nginx/sites-available/green-atlas /etc/nginx/sites-enabled/green-atlas
nginx -t
systemctl enable --now nginx
systemctl reload nginx
certbot certonly --webroot -w /var/www/green-atlas-acme -d "$domain" --non-interactive --agree-tos --register-unsafely-without-email
cp /opt/green-atlas/current/deploy/nginx.conf /etc/nginx/sites-available/green-atlas
nginx -t
systemctl reload nginx
install -d /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/green-atlas-nginx
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/green-atlas-nginx
systemctl enable --now certbot.timer
