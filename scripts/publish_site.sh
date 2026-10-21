#!/usr/bin/env bash
# site/ klasörünü GitHub Pages'e (gh-pages dalı, zorla) yayınlar.
# Önce siteyi üretin:  .venv/bin/python scripts/build_site.py
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f site/index.html ] || { echo "site/ yok: önce .venv/bin/python scripts/build_site.py"; exit 1; }
REMOTE="$(git remote get-url origin)"
NAME="$(git config user.name)"; MAIL="$(git config user.email)"; REV="$(git log --oneline -1)"
cd site
rm -rf .git
git init -q -b gh-pages
git add -A
git -c user.name="$NAME" -c user.email="$MAIL" commit -q -m "Site: $REV"
git push -q -f "$REMOTE" gh-pages
rm -rf .git
echo "Yayınlandı: https://umutcan-integrity.github.io/pharmacam/ (GitHub Pages 1-2 dk içinde güncellenir)"
