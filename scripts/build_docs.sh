#!/usr/bin/env bash
# docs/kilavuz.html -> docs/PharmaCam-Kurulum-Kilavuzu.pdf (Chrome headless)
set -euo pipefail
cd "$(dirname "$0")/.."
CH="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
"$CH" --headless=new --disable-gpu --no-pdf-header-footer --virtual-time-budget=4000 \
  --print-to-pdf="docs/PharmaCam-Kurulum-Kilavuzu.pdf" "file://$PWD/docs/kilavuz.html" 2>/dev/null
ls -la docs/PharmaCam-Kurulum-Kilavuzu.pdf
