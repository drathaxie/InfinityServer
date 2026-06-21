# Hosting InfinityServer on OCI (self-hosted Postgres + systemd)

The game server (`server.py`, TCP 5588) and web API (`webapi.py`, HTTP 8182) run on an OCI
Ubuntu 22.04 ARM VM as systemd services, backed by a self-hosted Postgres on the same VM.

Host (current deploy): `130.162.189.229` — Ubuntu 22.04.5 LTS, ARM64, 2 vCPU, 11 GiB RAM.
Code lives at `/opt/infinity` (`server/`, `data/`, `capture/`), venv at `/opt/infinity/.venv`.

## 1. Postgres (self-hosted, localhost-only)

```bash
sudo apt-get update && sudo apt-get install -y postgresql postgresql-contrib
# role + db (run as the postgres superuser; password from server/.pg.env)
sudo -u postgres psql -c "CREATE ROLE infinity LOGIN PASSWORD '<PGPASS>';"
sudo -u postgres createdb -O infinity infinity
```

Postgres listens on `127.0.0.1:5432` only (Ubuntu default) — never exposed publicly. db.py
connects over TCP localhost with password auth.

## 2. Code + deps

```bash
sudo mkdir -p /opt/infinity && sudo chown ubuntu:ubuntu /opt/infinity
# from a workstation with the repo (no git/rsync on the VM): stream it over ssh
tar czf - --exclude='__pycache__' --exclude='*.pyc' --exclude='server/.pg.env' \
    server data capture | ssh ubuntu@HOST "tar xzf - -C /opt/infinity"
# secrets separately (mode 600), then venv + deps
scp server/.pg.env ubuntu@HOST:/opt/infinity/server/.pg.env   # chmod 600
python3 -m venv /opt/infinity/.venv
/opt/infinity/.venv/bin/pip install -r /opt/infinity/server/requirements.txt
```

`server/.pg.env` (gitignored — never commit) holds the DB creds + the hosted repoint:

```
INFINITY_DB=postgres
INFINITY_PG_HOST=127.0.0.1
INFINITY_PG_PORT=5432
INFINITY_PG_DB=infinity
INFINITY_PG_USER=infinity
INFINITY_PG_PASSWORD=<secret>
INFINITY_PUBLIC_HOST=130.162.189.229   # what the account bundle hands clients for the game TCP
INFINITY_GAME_PORT=5588
```

## 3. Load schema + data

```bash
cd /opt/infinity/server && set -a && . ./.pg.env && set +a
/opt/infinity/.venv/bin/python migrate_to_pg.py    # ensures schema, copies data/infinity.db -> PG
```

`migrate_to_pg.py` is idempotent (TRUNCATE + reload, identity sequences advanced). For a fresh
authored-content-only DB instead, `db.init()` + `seed.run()` rebuilds everything from `data/`.

## 4. systemd services (survive reboot)

Copy `deploy/infinity-api.service` and `deploy/infinity-game.service` to
`/etc/systemd/system/`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now infinity-api infinity-game
systemctl is-active infinity-api infinity-game
```

Logs: `sudo journalctl -u infinity-game -f` (and `-u infinity-api`).

## 5. HTTPS for the API (REQUIRED — the client refuses plain HTTP)

Unity 6's `UnityWebRequest` blocks cleartext HTTP to any non-localhost host ("Insecure connection
not allowed"), and the client builds *every* API call from `Main.WebApiURL` — so the web API MUST
be HTTPS. (The game TCP :5588 is a raw socket and is unaffected.) We terminate TLS with Caddy
using a free `sslip.io` hostname (auto-resolves to the IP, no DNS setup) + Let's Encrypt:

```bash
# install Caddy (official apt repo)
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
# reverse-proxy the sslip hostname to the API; Caddy auto-provisions the cert
printf '130-162-189-229.sslip.io {\n\treverse_proxy localhost:8182\n}\n' | sudo tee /etc/caddy/Caddyfile
sudo systemctl restart caddy   # enabled by default -> survives reboot
```

## 6. Firewall (two layers)

- **VM (iptables):** insert ACCEPT before the default REJECT, then persist:
  ```bash
  for p in 5588 80 443; do sudo iptables -I INPUT 5 -p tcp --dport $p -j ACCEPT; done
  sudo apt-get install -y iptables-persistent && sudo netfilter-persistent save
  ```
  (8182 need not be public — Caddy reaches it over localhost — but it's harmless if open.)
- **OCI security list / NSG (Console):** stateful ingress, Source `0.0.0.0/0`, TCP, ports
  **5588** (game), **80** + **443** (Caddy/Let's Encrypt + HTTPS API). DB port 5432 stays closed.

## 7. Repoint the client

Set `UserData/infinity_api.txt` to `https://130-162-189-229.sslip.io/`. The mod rewrites
`Main.WebApiURL` to it, so all API calls go over HTTPS; the account bundle hands the client the
game server at `130.162.189.229:5588` (raw TCP).

## Notes

- The `.unity3d` asset bundles still stream from AE's own public CDN — we host none of them.
- The mod also carries a WebCom insecure-HTTP fallback (for an http:// localhost API), inert
  when the URL is https://.
