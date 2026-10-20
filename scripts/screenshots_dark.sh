#!/usr/bin/env bash
# Koyu tema panel görüntüleri (tanıtım sitesi için). Demo sitesi :8090'da açık olmalı:
#   python -m http.server 8090 -d site
set -euo pipefail
URL="${1:-http://localhost:8090/demo}"
OUT="${2:-scripts/site/assets}"
DAY="${DAY:-2026-10-16}"
CH="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
mkdir -p "$OUT"
shot() {
  "$CH" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
    --window-size=1280,"${3:-900}" --virtual-time-budget=5000 \
    --screenshot="$OUT/$1.png" "$URL/?theme=dark&shot=1&$2" >/dev/null 2>&1
  echo "$OUT/$1.png"
}
shot dark-canli "#live" 1180
shot dark-analiz "day=$DAY#analysis" 900
shot dark-raflar "days=30#shelves" 900
shot dark-sira "day=$DAY#queue" 900
shot dark-isi "days=7#heatmap" 900
shot dark-tahmin "#forecast" 900
shot dark-bolgeler "#zones" 900
