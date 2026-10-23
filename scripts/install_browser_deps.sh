#!/usr/bin/env bash
# Ubuntu 24.04 user-local alternative to `playwright install-deps chromium`.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .local/packages .local/browser-deps
cd .local/packages
apt-get download libnspr4 libnss3 libasound2t64
for pkg in ./*.deb; do dpkg-deb -x "$pkg" ../browser-deps; done
