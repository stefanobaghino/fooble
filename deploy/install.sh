#!/bin/bash
# Install or update fooble under /opt/fooble on a Debian host. Run as a sudoer.
#   deploy/install.sh [git-ref]      (default: main)
set -euo pipefail
REF=${1:-main}
REPO_URL=https://github.com/stefanobaghino/fooble.git
APP=/opt/fooble/app
DATA=/opt/fooble/data
PY=/opt/fooble/python
HERE=$(cd "$(dirname "$0")/.." && pwd)

id fooble >/dev/null 2>&1 || sudo useradd --system --home-dir /opt/fooble --shell /usr/sbin/nologin fooble
sudo install -d -o "$USER" -g "$USER" /opt/fooble "$APP" "$PY"
sudo install -d -o fooble -g fooble "$DATA"

if [ ! -d "$APP/.git" ]; then
  git clone -q "$HERE" "$APP"
  git -C "$APP" remote set-url origin "$REPO_URL"
fi
git -C "$APP" fetch -q "$HERE" "$REF" && git -C "$APP" checkout -q --detach FETCH_HEAD
# The managed interpreter must live where the service user can read it, not under ~/.local.
UV_PYTHON_INSTALL_DIR="$PY" uv sync --locked --project "$APP" --no-dev
chmod -R a+rX /opt/fooble/app /opt/fooble/python

sudo install -m 644 "$HERE"/deploy/fooble.service "$HERE"/deploy/fooble-crawl.service "$HERE"/deploy/fooble-crawl.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fooble.service fooble-crawl.timer
sudo systemctl restart fooble.service
systemctl --no-pager --lines=0 status fooble.service fooble-crawl.timer | grep -E '^\S|Active:'
