#!/usr/bin/env bash
#
# Temporarily exposes the locally running frontend (which proxies /api to
# the locally running backend -- see frontend/vite.config.ts) to the
# internet over a Cloudflare Quick Tunnel, so other people can reach it
# for testing.
#
# This does NOT start the app for you. Before running this script, start
# the app the normal way (see SETUP.md):
#   1. Postgres running, migrations applied
#   2. Backend:  uvicorn app.main:app --reload            (port 8000)
#   3. Frontend: cd frontend && npm run dev                (port 5173)
#
# Only the frontend dev server port is exposed. The backend (8000) and
# Postgres are never bound to anything but localhost, so nothing else on
# this machine becomes reachable through the tunnel.
#
# This uses `cloudflared tunnel --url ...`, a "Quick Tunnel": no
# Cloudflare account, login, or credentials are required or created. It
# prints a random, temporary https://<random>.trycloudflare.com URL that
# is torn down the moment you stop this script (Ctrl+C) -- nothing
# persists, and there is nothing here that could end up committed to git.
#
# Usage:
#   scripts/start_tunnel.sh
# Stop:
#   Ctrl+C (or close the terminal running it)

set -euo pipefail

LOCAL_PORT="${TUNNEL_LOCAL_PORT:-5173}"
LOCAL_URL="http://127.0.0.1:${LOCAL_PORT}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is not installed." >&2
  echo "Install it, then re-run this script:" >&2
  echo "  macOS:          brew install cloudflared" >&2
  echo "  Debian/Ubuntu:  https://pkg.cloudflare.com/index.html#deb-repo-setup" >&2
  echo "  Other/manual:   https://github.com/cloudflare/cloudflared/releases" >&2
  exit 1
fi

if ! curl -sf -o /dev/null --max-time 3 "$LOCAL_URL"; then
  echo "Nothing is responding on ${LOCAL_URL} yet." >&2
  echo "Start the frontend dev server first: cd frontend && npm run dev" >&2
  echo "(and make sure the backend is running too -- the frontend proxies /api to it)." >&2
  exit 1
fi

echo "Local app found on ${LOCAL_URL}. Starting Cloudflare Quick Tunnel..."
echo "This is temporary and unauthenticated: anyone who has the URL below"
echo "can reach your app while this is running. Share it only with the"
echo "people who should be testing it, and stop this script when done."
echo

exec cloudflared tunnel --no-autoupdate --url "$LOCAL_URL"
