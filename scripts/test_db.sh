#!/usr/bin/env bash
# Throwaway PostgreSQL cluster for the test suite.
#
# The database-backed tests need a real PostgreSQL — the models use
# Postgres arrays and the concurrency tests need real row locks, so
# SQLite cannot stand in. This starts a private cluster owned by the
# current user on a non-standard port: no sudo, no interference with a
# system PostgreSQL, and nothing shared with production.
#
#   ./scripts/test_db.sh start     # then export the printed URL
#   ./scripts/test_db.sh stop
#   ./scripts/test_db.sh nuke      # stop and delete the data directory
#
# NEVER point TEST_DATABASE_URL at Neon. tests/conftest.py refuses such
# a URL outright, because the suite drops and recreates every table.
set -euo pipefail

PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
PORT="${TEST_PG_PORT:-55432}"
DATA="${TEST_PG_DATA:-${TMPDIR:-/tmp}/cinemahub-testdb}"
# Socket directory kept short deliberately: the Unix socket path has a
# 107-byte limit and a long TMPDIR silently breaks startup.
SOCK="/tmp/chpg-$PORT"
DB="cinemahub_test"
URL="postgresql+asyncpg://testrunner@127.0.0.1:${PORT}/${DB}"

case "${1:-start}" in
  start)
    mkdir -p "$SOCK"
    if [ ! -d "$DATA/base" ]; then
      "$PGBIN/initdb" -D "$DATA" -U testrunner --auth=trust -E UTF8 >/dev/null
    fi
    if ! "$PGBIN/pg_isready" -h 127.0.0.1 -p "$PORT" -q 2>/dev/null; then
      "$PGBIN/pg_ctl" -D "$DATA" \
        -o "-p $PORT -k $SOCK -c listen_addresses=127.0.0.1" \
        -l "$DATA/server.log" start
      sleep 2
    fi
    "$PGBIN/createdb" -h 127.0.0.1 -p "$PORT" -U testrunner "$DB" 2>/dev/null || true
    echo "ready. run:"
    echo "  export TEST_DATABASE_URL='$URL'"
    ;;
  stop)
    "$PGBIN/pg_ctl" -D "$DATA" stop -m fast || true
    ;;
  nuke)
    "$PGBIN/pg_ctl" -D "$DATA" stop -m immediate 2>/dev/null || true
    rm -rf "$DATA" "$SOCK"
    echo "removed $DATA"
    ;;
  *)
    echo "usage: $0 {start|stop|nuke}" >&2
    exit 1
    ;;
esac
