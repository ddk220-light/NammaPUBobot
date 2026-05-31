#!/usr/bin/env bash
#
# Database backup for NammaPUBobot.
#
# Ported and adapted from BombayBot's backup_db.sh. BombayBot dumped a local
# Docker MariaDB container and shipped to a "milesweb" remote; NammaPUBobot runs
# on Railway's managed MySQL, so this version connects over the network using
# the same DB_URI the bot uses (or Railway's MYSQL* plugin variables), writes a
# timestamped gzipped dump, and prunes dumps older than the retention window.
#
# Usage:
#   DB_URI="mysql://user:pass@host:port/dbname" ./scripts/backup_db.sh
#   # or rely on Railway's MYSQLHOST/MYSQLUSER/... environment variables
#   # optional: BACKUP_DIR=/path RETENTION_DAYS=30 ./scripts/backup_db.sh
#
# Requires: mysqldump, gzip (mysql-client).

set -euo pipefail

# Load a local .env if present (handy for cron on a self-hosted box). On
# Railway the variables are already in the environment, so this is optional.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

BACKUP_DIR="${BACKUP_DIR:-./db_backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

# ─────────────────────────────────────────────
# Resolve connection details
# ─────────────────────────────────────────────
# Prefer an explicit DB_URI (mysql://user:pass@host:port/db); otherwise fall
# back to Railway's MySQL plugin variables.
if [ -n "${DB_URI:-}" ]; then
  # Strip scheme, then parse user:pass@host:port/db without needing extra tools.
  _uri="${DB_URI#mysql://}"
  _creds="${_uri%%@*}"        # user:pass
  _hostpart="${_uri#*@}"      # host:port/db
  DB_USER="${_creds%%:*}"
  DB_PASSWORD="${_creds#*:}"
  _hostport="${_hostpart%%/*}"   # host:port
  DB_NAME="${_hostpart#*/}"      # db (may include ?params)
  DB_NAME="${DB_NAME%%\?*}"
  DB_HOST="${_hostport%%:*}"
  if [ "$_hostport" = "$DB_HOST" ]; then
    DB_PORT="3306"
  else
    DB_PORT="${_hostport##*:}"
  fi
else
  DB_HOST="${MYSQLHOST:-${MYSQL_HOST:-}}"
  DB_PORT="${MYSQLPORT:-${MYSQL_PORT:-3306}}"
  DB_USER="${MYSQLUSER:-${MYSQL_USER:-}}"
  DB_PASSWORD="${MYSQLPASSWORD:-${MYSQL_PASSWORD:-}}"
  DB_NAME="${MYSQLDATABASE:-${MYSQL_DATABASE:-}}"
fi

if [ -z "${DB_HOST:-}" ] || [ -z "${DB_USER:-}" ] || [ -z "${DB_NAME:-}" ]; then
  echo "ERROR: could not resolve DB connection. Set DB_URI or MYSQL* variables." >&2
  exit 1
fi

# ─────────────────────────────────────────────
# Run the dump
# ─────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"
DATE_STAMP="$(date +%Y_%m_%d_%H_%M_%S)"
BACKUP_FILE="${BACKUP_DIR}/${DB_NAME}-${DATE_STAMP}.sql"

echo "Backing up ${DB_NAME} from ${DB_HOST}:${DB_PORT} ..."
# MYSQL_PWD avoids the insecure "password on the command line" warning and
# keeps the secret out of the process list.
MYSQL_PWD="$DB_PASSWORD" mysqldump \
  --host="$DB_HOST" \
  --port="$DB_PORT" \
  --user="$DB_USER" \
  --single-transaction \
  --quick \
  --default-character-set=utf8mb4 \
  "$DB_NAME" > "$BACKUP_FILE"

gzip -f "$BACKUP_FILE"
echo "Backup written: ${BACKUP_FILE}.gz"

# ─────────────────────────────────────────────
# Prune old backups
# ─────────────────────────────────────────────
echo "Pruning backups older than ${RETENTION_DAYS} days..."
find "$BACKUP_DIR" -type f -name "${DB_NAME}-*.sql.gz" -mtime "+${RETENTION_DAYS}" -print -delete

echo "Done."
