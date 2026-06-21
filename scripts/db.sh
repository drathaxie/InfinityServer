#!/usr/bin/env bash
# scripts/db.sh — query / edit the LIVE InfinityServer Postgres with nothing installed locally.
# Runs psql ON the VM over SSH (the DB only listens on the VM's localhost, by design).
#
#   ./scripts/db.sh                                   # interactive psql shell
#   ./scripts/db.sh "SELECT id,name FROM characters"  # one-off query
#   ./scripts/db.sh -f scripts/sql/whoami.sql         # run a .sql file
#   ./scripts/db.sh --csv "SELECT * FROM characters"  # CSV output
#
# Overrides: INFINITY_SSH_KEY, INFINITY_VM
set -euo pipefail
KEY="${INFINITY_SSH_KEY:-$HOME/Downloads/ssh-key-2026-06-19.key}"
[ -f "$KEY" ] || KEY="/c/Users/jesse/Downloads/ssh-key-2026-06-19.key"   # git-bash on Windows
VM="${INFINITY_VM:-ubuntu@130.162.189.229}"

PRELUDE='cd /opt/infinity/server && set -a && . ./.pg.env && set +a && export PGPASSWORD=$INFINITY_PG_PASSWORD && '
PSQL='psql -h $INFINITY_PG_HOST -p $INFINITY_PG_PORT -U $INFINITY_PG_USER -d $INFINITY_PG_DB'
FLAGS='-v ON_ERROR_STOP=1 -P pager=off'

CSV=""
if [ "${1:-}" = "--csv" ]; then CSV="--csv"; shift; fi

if [ "${1:-}" = "-f" ]; then
  ssh -i "$KEY" -o StrictHostKeyChecking=no "$VM" "$PRELUDE$PSQL $FLAGS $CSV" < "$2"
elif [ -n "${1:-}" ]; then
  echo "$*" | ssh -i "$KEY" -o StrictHostKeyChecking=no "$VM" "$PRELUDE$PSQL $FLAGS $CSV"
else
  ssh -t -i "$KEY" -o StrictHostKeyChecking=no "$VM" "${PRELUDE}exec $PSQL"
fi
