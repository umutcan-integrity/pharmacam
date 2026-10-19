#!/usr/bin/env bash
# PharmaCam — Raspberry Pi 5 + AI Camera kurulum betiği
#
#   curl -sSL https://raw.githubusercontent.com/umutcan-integrity/pharmacam/main/deploy/install.sh | bash
# veya depo klonlandıysa:
#   ./deploy/install.sh
#
# Yaptıkları: sistem paketleri + IMX500 modelleri, /opt/pharmacam altına kurulum,
# Python sanal ortamı, yapılandırma, systemd servisi. Tekrar çalıştırmak güvenlidir
# (güncelleme olarak çalışır; yapılandırma ve veriler korunur).
set -euo pipefail

REPO_URL="${PHARMACAM_REPO:-https://github.com/umutcan-integrity/pharmacam.git}"
BRANCH="${PHARMACAM_BRANCH:-main}"
PREFIX="${PHARMACAM_PREFIX:-/opt/pharmacam}"
RUN_USER="${PHARMACAM_USER:-${SUDO_USER:-$(id -un)}}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd || true)"

say()  { printf "\033[1;32m==>\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!!\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31mHATA:\033[0m %s\n" "$*" >&2; exit 1; }

[ "$(uname -m)" = "aarch64" ] || warn "Bu betik Raspberry Pi 5 (aarch64) için yazıldı; devam ediliyor."
command -v sudo >/dev/null || die "sudo gerekli"

say "Sistem paketleri kuruluyor (birkaç dakika sürebilir)..."
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  imx500-all python3-picamera2 python3-opencv python3-numpy python3-yaml python3-matplotlib \
  python3-venv python3-pip git rsync >/dev/null

say "Kod $PREFIX dizinine yerleştiriliyor..."
sudo mkdir -p "$PREFIX"
sudo chown "$RUN_USER":"$RUN_USER" "$PREFIX"
if [ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/pharmacam/__init__.py" ] && [ "$SRC_DIR" != "$PREFIX" ]; then
  rsync -a --delete --exclude .git --exclude .venv --exclude data --exclude 'config/pharmacam.yaml' \
    "$SRC_DIR"/ "$PREFIX"/
elif [ -d "$PREFIX/.git" ]; then
  git -C "$PREFIX" pull --ff-only
elif [ ! -f "$PREFIX/pharmacam/__init__.py" ]; then
  git clone --depth 1 -b "$BRANCH" "$REPO_URL" "$PREFIX"
fi

say "Python ortamı hazırlanıyor..."
# --system-site-packages: picamera2/libcamera/opencv apt'tan gelir
[ -d "$PREFIX/venv" ] || python3 -m venv --system-site-packages "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q -r "$PREFIX/requirements-pi.txt"

mkdir -p "$PREFIX/data"
if [ ! -f "$PREFIX/config/pharmacam.yaml" ]; then
  say "Yapılandırma oluşturuluyor..."
  cp "$PREFIX/config/pharmacam.example.yaml" "$PREFIX/config/pharmacam.yaml"
  SERIAL="$(tr -d '\0' </proc/device-tree/serial-number 2>/dev/null | tail -c 6 || echo 000000)"
  PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(9))')"
  sed -i "s/^  id: pharmacam-001.*/  id: pharmacam-${SERIAL}/" "$PREFIX/config/pharmacam.yaml"
  sed -i "s/^  password: \"degistir-beni\".*/  password: \"${PASS}\"/" "$PREFIX/config/pharmacam.yaml"
  echo "$PASS" > "$PREFIX/data/.panel-password"
  chmod 600 "$PREFIX/data/.panel-password" "$PREFIX/config/pharmacam.yaml"
fi

say "Kamera kontrol ediliyor..."
if rpicam-hello --list-cameras 2>&1 | grep -q imx500; then
  say "AI Camera (IMX500) bulundu."
else
  warn "AI Camera bulunamadı! Pi'yi kapatıp kamera kablosunu kontrol edin (kılavuz bölüm 3). Servis yine kurulacak."
fi

say "systemd servisi kuruluyor..."
sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@USER@|$RUN_USER|g" "$PREFIX/deploy/pharmacam.service" \
  | sudo tee /etc/systemd/system/pharmacam.service >/dev/null
sudo usermod -aG video "$RUN_USER" || true
sudo systemctl daemon-reload
sudo systemctl enable pharmacam.service >/dev/null
sudo systemctl restart pharmacam.service

IP="$(hostname -I | awk '{print $1}')"
PORT="$(grep -E '^\s+port:' "$PREFIX/config/pharmacam.yaml" | head -1 | awk '{print $2}')"
echo
say "Kurulum tamamlandı!"
echo "    Panel:      http://${IP}:${PORT:-8080}   (veya http://$(hostname).local:${PORT:-8080})"
echo "    Kullanıcı:  admin"
[ -f "$PREFIX/data/.panel-password" ] && echo "    Şifre:      $(cat "$PREFIX/data/.panel-password")   (config/pharmacam.yaml içinden değiştirilebilir)"
echo "    Loglar:     journalctl -u pharmacam -f"
echo "    Kamera testi: $PREFIX/venv/bin/python -m pharmacam -c $PREFIX/config/pharmacam.yaml check"
