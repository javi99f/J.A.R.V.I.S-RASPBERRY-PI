#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:0}"
cd "$(dirname "$0")"

rotation="left"
display_mode="1024x600"
if [ -f .env ]; then
  configured_rotation="$(sed -n 's/^DISPLAY_ROTATION=//p' .env | tail -n 1 | tr -d '\r\"' | xargs)"
  if [[ "$configured_rotation" =~ ^(normal|left|right|inverted)$ ]]; then
    rotation="$configured_rotation"
  fi
  configured_mode="$(sed -n 's/^DISPLAY_RESOLUTION=//p' .env | tail -n 1 | tr -d '\r\"' | xargs)"
  if [[ "$configured_mode" =~ ^[0-9]+x[0-9]+$ ]]; then
    display_mode="$configured_mode"
  fi
fi

display_output="$(xrandr --query | awk '$2 == "connected" { print $1; exit }')"
if [ -n "$display_output" ]; then
  if xrandr --query | grep -Eq "^[[:space:]]+${display_mode}([[:space:]]|$)"; then
    xrandr --output "$display_output" --mode "$display_mode" --rotate "$rotation"
  else
    echo "[JARVIS] Display mode $display_mode is not advertised; applying rotation only."
    xrandr --output "$display_output" --rotate "$rotation"
  fi
fi
xsetroot -solid '#000000'

# Map common absolute touch controllers after the display rotation. Relative
# mice are deliberately left alone.
if command -v xinput >/dev/null 2>&1 && [ -n "$display_output" ]; then
  while IFS= read -r line; do
    if printf '%s\n' "$line" | grep -Eiq 'touch|touchscreen|goodix|ilitek|egalax|usb2iic|(^|[_ -])ctp|ft[0-9]+|raspberrypi-ts|hid-multitouch|ads7846'; then
      device_id="$(printf '%s\n' "$line" | sed -n 's/.*id=\([0-9][0-9]*\).*/\1/p')"
      if [ -n "$device_id" ]; then
        xinput map-to-output "$device_id" "$display_output" || true
      fi
    fi
  done < <(xinput --list --short)
fi

if command -v unclutter >/dev/null 2>&1; then
  unclutter --timeout 0.2 --hide-on-touch --start-hidden --fork || true
fi

# Keep the graphical X session alive across a verified self-update.  Runtime
# exits with code 75 only after the new files pass validation.
while true; do
  set +e
  ./launch_assistant.sh
  status=$?
  set -e
  if [ "$status" -eq 75 ]; then
    echo "[JARVIS] Update installed; restarting the assistant..."
    sleep 1
    continue
  fi
  exit "$status"
done
