#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
sudo apt update
sudo apt install -y xinput unclutter-xfixes
chmod +x start_jarvis_pi.sh launch_assistant.sh

cat > "$HOME/.xinitrc" <<EOF
#!/usr/bin/env bash
exec "$HOME/Jarvis/start_jarvis_pi.sh"
EOF
chmod +x "$HOME/.xinitrc"

touch .env
if grep -q '^DISPLAY_ROTATION=' .env; then
  sed -i 's/^DISPLAY_ROTATION=.*/DISPLAY_ROTATION=left/' .env
else
  printf '\nDISPLAY_ROTATION=left\n' >> .env
fi

if grep -q '^DISPLAY_RESOLUTION=' .env; then
  sed -i 's/^DISPLAY_RESOLUTION=.*/DISPLAY_RESOLUTION=1024x600/' .env
else
  printf 'DISPLAY_RESOLUTION=1024x600\n' >> .env
fi

echo "Display setup complete: physical 1024x600, portrait 600x1024, rotation left."
echo "Run: startx"
