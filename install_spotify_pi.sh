#!/usr/bin/env bash
set -euo pipefail

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This installer requires Raspberry Pi OS or another Debian-based system." >&2
  exit 1
fi

case "$(uname -m)" in
  aarch64|armv7l|x86_64|riscv64) ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

sudo apt-get update
sudo apt-get install -y curl ca-certificates

tmp_key="$(mktemp)"
trap 'rm -f "$tmp_key"' EXIT
curl --fail --silent --show-error --location \
  https://dtcooper.github.io/raspotify/key.asc --output "$tmp_key"
sudo install -m 0644 "$tmp_key" /usr/share/keyrings/raspotify_key.asc
echo "deb [signed-by=/usr/share/keyrings/raspotify_key.asc] https://dtcooper.github.io/raspotify raspotify main" \
  | sudo tee /etc/apt/sources.list.d/raspotify.list >/dev/null
sudo apt-get update
sudo apt-get install -y raspotify

if [[ -f /etc/raspotify/conf ]]; then
  if grep -q '^LIBRESPOT_NAME=' /etc/raspotify/conf; then
    sudo sed -i 's/^LIBRESPOT_NAME=.*/LIBRESPOT_NAME="JARVIS Raspberry Pi"/' /etc/raspotify/conf
  else
    echo 'LIBRESPOT_NAME="JARVIS Raspberry Pi"' | sudo tee -a /etc/raspotify/conf >/dev/null
  fi
fi

sudo systemctl enable --now raspotify
"$(dirname "$0")/.venv/bin/python" -m pip install -r "$(dirname "$0")/requirements.txt"

echo
echo "Raspotify and Spotipy are installed."
echo "Next run: cd ~/Jarvis && ./.venv/bin/python configure_spotify.py"
