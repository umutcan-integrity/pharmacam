#!/usr/bin/env bash
# Panel ekran görüntüleri (kılavuz ve site için).
# Önce demo sunucusunu başlatın:  python -m pharmacam -c config/pharmacam.demo.yaml run
set -euo pipefail
URL="${1:-http://localhost:8080}"
OUT="${2:-docs/assets}"
DAY="${DAY:-2026-10-16}"
CH="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
mkdir -p "$OUT"
shot() {  # name, query+hash, height
  "$CH" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
    --window-size=1280,"${3:-900}" --virtual-time-budget=7000 --timeout=20000 \
    --screenshot="$OUT/$1.png" "$URL/?theme=light&shot=1&$2" >/dev/null 2>&1
  echo "$OUT/$1.png"
}
shot panel-canli "#live" 1180
shot panel-analiz "day=$DAY#analysis" 1250
shot panel-raflar "days=30#shelves" 900
shot panel-sira "day=$DAY#queue" 900
shot panel-isi "days=7#heatmap" 1000
shot panel-tahmin "#forecast" 1000
shot panel-ziyaret "day=$DAY#visits" 760
shot panel-bolgeler "#zones" 760
shot panel-ayarlar "#settings" 640
shot panel-raporlar "#reports" 640
shot panel-profil "days=30#profile" 1100
shot panel-kurulum "#setup" 1250
