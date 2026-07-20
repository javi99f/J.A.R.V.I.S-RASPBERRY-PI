# J.A.R.V.I.S RASPBERRY PI

A Raspberry Pi voice assistant built for a vertical 600x1024 touchscreen. It uses a cinematic full-screen HUD, Gemini Live voice conversation, optional OpenRouter helper actions, optional Zernio social analytics, Pi device controls, and optional Home Assistant smart-home control.

This customized branch adds a privacy-preserving local `Hey Jarvis` wake word,
a Siri-style follow-up conversation window, explicit audio-device selection,
microphone diagnostics, generic Bluetooth configuration, and a Raspberry Pi 4
installer. See `RASPBERRY_PI_4_TEST.md` for the physical test procedure.

This project is intentionally Pi-first. It does not include desktop automation, browser control, mouse/keyboard control, file management, games, or Windows/macOS actions.

## Features

- Real-time voice conversation through Gemini Live
- Full-screen PyQt6 HUD for a 600x1024 vertical Raspberry Pi touchscreen
- Local wake-word standby; room audio is not sent to Gemini until activation
- Five-second contextual follow-up window after every Jarvis response
- Password-protected developer diagnostics and approved sensitive settings
- Touch settings for persistent history, audio routing, personality, and Gemini Live voice
- Speaker volume control, speaker mute/unmute, and optional generic Bluetooth reconnect helper
- Assistant listening mute/unmute with voice-safe wake phrases
- UI brightness dimming for HDMI touchscreens that do not expose Linux backlight control
- Optional Home Assistant controls for lights, lamps, LED strips, switches, and smart plugs
- Optional Zernio-powered Instagram/TikTok analytics through natural language
- Optional OpenRouter-backed public question answering and helper responses
- Secure remote updates from public GitHub Releases, with confirmation, backup, validation, and rollback

## Hardware You Need

- Raspberry Pi running Raspberry Pi OS with a desktop session
- Vertical touchscreen with a 600x1024 logical resolution
- USB microphone
- Speaker output through USB, HDMI, Bluetooth, or 3.5 mm audio
- Internet access

## Accounts And Keys

Create one local setup file: `.env`.

That is the only file a normal user needs to edit for their own keys, URLs, and tokens. Copy it from `.env.example`, fill in their own values, and keep it private.

Required:

- `GEMINI_API_KEY` - used for the live voice assistant
- `OPENROUTER_API_KEY` - used for helper answering and fallback model calls

Optional:

- `ZERNIO_API_KEY` - enables Instagram/TikTok analytics questions
- `HOME_ASSISTANT_URL` - your Home Assistant base URL
- `HOME_ASSISTANT_TOKEN` - a Home Assistant long-lived access token

Never commit `.env` or real credentials. This repo ignores `.env`, local config files, runtime state, memory JSON, logs, and PID files.

## Install On Raspberry Pi

From a terminal on the Pi:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip portaudio19-dev pulseaudio-utils
git clone <your-repo-url> omar-ai-core
cd omar-ai-core
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
nano .env
```

All user-specific setup lives in `.env`.

Run it:

```bash
python -m omar_ai_core
```

Or use the launch script:

```bash
chmod +x launch_assistant.sh
./launch_assistant.sh
```

## Configure The Display

The HUD is designed for a portrait 600x1024 desktop obtained by rotating the physical 1024x600 panel. If your display opens sideways, rotate it in Raspberry Pi OS display settings or set the display orientation from your Pi's screen configuration tool.

## Developer Mode, Personality And Voice

Say `Hey Jarvis, modo desarrollador`. Jarvis opens a local masked text dialog;
the password is verified on the Raspberry Pi and is never sent to Gemini. The
authorized session lasts 30 minutes. The initial password is the one selected
privately by the owner; change its SHA-256 value in `.env` before sharing the device.

Every developer analysis, rejected attempt, tool request, and real sensitive
change is written to the `AJUSTES` → `HISTORIAL` → `ACCIONES DEV` audit. Entries
are chained with SHA-256 so later edits are detected. A diagnostic analysis is
read-only: Jarvis may recommend a correction but cannot claim it was applied.
Real changes always report an audit event ID and the exact setting or file changed.

After unlocking, open `AJUSTES` → `PERSONA` to edit speaking style and choose a
Gemini Live voice. The base behavior remains in
`omar_ai_core/persona/system_prompt.txt`; user preferences are stored separately
in `config/personality_style.txt`, so they cannot erase the essential safety and
privacy rules. The selected voice is stored as `JARVIS_VOICE` in `.env`.

The current voice is `Charon`. Other useful choices include `Kore` or `Orus`
(firm), `Puck` or `Laomedeia` (upbeat), `Gacrux` (mature), and `Sulafat` (warm).

After Jarvis finishes speaking, a fixed five-second contextual window accepts a
direct follow-up. Ambient speech cannot extend that deadline. Speech addressed
to Siri, Alexa, Google, Bixby, another assistant, or another person is ignored.

The app uses an in-app dim overlay for brightness because many HDMI touchscreens do not expose a hardware backlight device to Linux.

## Configure Audio

Check that Linux sees your microphone and speaker:

```bash
arecord -l
aplay -l
pactl list short sources
pactl list short sinks
```

Set the default output device from Raspberry Pi OS audio settings or with `pactl`. Then restart the assistant.

Voice commands for speaker output:

- "mute volume"
- "unmute volume"
- "set volume to 30 percent"
- "volume up"
- "volume down"

## Assistant Mute Vs Speaker Mute

The assistant has two separate mute systems so voice commands do not accidentally disable the wrong thing.

Assistant listening mute:

- "mute yourself"
- "stop listening"
- "JARVIS unmute"
- "JARVIS wake up"
- "JARVIS listen"

When listening mute is enabled, JARVIS ignores normal commands and only listens for the wake phrases above.

Speaker mute:

- "mute volume"
- "mute speaker"
- "unmute volume"
- "unmute speaker"

SSH fallback:

```bash
./assistantctl mute
./assistantctl unmute
./assistantctl status
```

## Home Assistant Setup

Home Assistant is optional. To enable it:

1. Open Home Assistant.
2. Go to your user profile.
3. Create a long-lived access token.
4. Add these values to `.env`:

```bash
HOME_ASSISTANT_URL=http://homeassistant.local:8123
HOME_ASSISTANT_TOKEN=your-home-assistant-long-lived-access-token
```

Example commands:

- "turn on the table lamp"
- "turn off the studio lights"
- "toggle the LED strip"
- "are the kitchen lights on?"
- "list my lights"

Supported Home Assistant entity domains include `light` and `switch`.

## Spotify on the Raspberry Pi

Spotify support has two local parts:

- `Raspotify` makes the Raspberry appear as the Spotify Connect speaker named
  `JARVIS Raspberry Pi` and sends music to the Pi's configured ALSA output.
- `Spotipy` lets JARVIS search and control that real player through Spotify's
  Web API. Playback control requires the app owner's active Spotify Premium account.

Install the receiver and Python dependency once:

```bash
cd ~/Jarvis
chmod +x install_spotify_pi.sh
./install_spotify_pi.sh
```

Create a personal app in the Spotify Developer Dashboard and add this exact
redirect URI to its settings:

```text
http://127.0.0.1:8888/callback
```

Then authorize JARVIS without placing secrets in shell history:

```bash
cd ~/Jarvis
./.venv/bin/python configure_spotify.py
```

The helper stores the Client ID and Client Secret only in the ignored `.env`
file and stores the refresh token in the ignored
`config/spotify_token_cache.json` file. Never publish either file.

Useful checks:

```bash
systemctl status raspotify --no-pager
journalctl -u raspotify -n 50 --no-pager
```

Example commands:

- "pon Blinding Lights en Spotify"
- "pon música de Queen"
- "pausa Spotify"
- "pausa"
- "siguiente canción"
- "qué está sonando"
- "pon el volumen al 40 por ciento" (changes the Raspberry Pi system volume)

The portrait HUD shows a compact Spotify player below the central JARVIS
circle. It displays the real track and artist and provides touch controls for
previous, pause/resume, and next. Spoken volume requests always change the Pi's
general output volume rather than a separate Spotify-only volume.

## Zernio Social Analytics Setup

Zernio is optional. Add your key to `.env`:

```bash
ZERNIO_API_KEY=your-zernio-api-key
```

Example questions:

- "How many followers do we have on Instagram?"
- "How did the last two Instagram posts perform?"
- "What was the average engagement rate?"
- "How many likes and comments did the latest TikTok get?"

## Optional Autostart

To start the assistant automatically when the Pi desktop opens:

```bash
mkdir -p ~/.config/autostart
nano ~/.config/autostart/omar-ai-core.desktop
```

Paste this, changing the path if you cloned somewhere else:

```ini
[Desktop Entry]
Type=Application
Name=Omar AI Core
Exec=/home/pi/omar-ai-core/launch_assistant.sh
WorkingDirectory=/home/pi/omar-ai-core
Terminal=false
X-GNOME-Autostart-enabled=true
```

Reboot the Pi:

```bash
sudo reboot
```

## Remote Updates

Version 0.1.1 can check and install Raspberry Pi updates published through a
public GitHub repository. Configure the repository in `.env`:

```env
UPDATE_REPOSITORY=javi99f/J.A.R.V.I.S-RASPBERRY-PI
UPDATE_ALLOW_PRERELEASE=0
```

Then type or say "Busca actualizaciones de Jarvis", or press `UPDATE` on the
Pi interface. Installation always requires explicit confirmation. Local keys,
memory, visual settings, and audio configuration are preserved. See
`UPDATES_GITHUB.md` for the publishing and recovery workflow.

## Touch Settings

Press `AJUSTES` in the bottom bar to open the Raspberry Pi settings surface:

- `HISTORIAL` shows conversations, recent errors, and the tamper-evident `ACCIONES DEV` audit.
- `AUDIO` selects the PortAudio input (microphone) and output (speaker).
- `GENERAL` shows the local activation phrase: `Hey Jarvis`.

Audio selections are saved in `.env` and applied without rebooting the Pi.
Use `VOLVER A BUSCAR DISPOSITIVOS` after connecting a USB or Bluetooth device.

## Project Layout

- `omar_ai_core/runtime.py` - Gemini Live runtime and tool routing
- `omar_ai_core/updater.py` - GitHub Release checking, verified installation, backup, and rollback
- `omar_ai_core/display/hud.py` - touchscreen HUD
- `omar_ai_core/tools/pi_device.py` - Pi speaker volume, brightness, mute, and Era 300 control
- `omar_ai_core/tools/home_control.py` - Home Assistant lights and switches
- `omar_ai_core/tools/spotify_control.py` - Spotify search, playback, device selection, and volume
- `omar_ai_core/tools/social_metrics.py` - Zernio Instagram/TikTok analytics
- `omar_ai_core/tools/web_lookup.py` - public lookup helper
- `omar_ai_core/state/listening.py` - shared listening mute state for voice and SSH control
- `omar_ai_core/memory/` - lightweight local memory helpers
- `omar_ai_core/persona/system_prompt.txt` - assistant behavior prompt
- `assistantctl` - SSH command-line listening mute control
- `launch_assistant.sh` - Raspberry Pi desktop launch helper
- `.github/workflows/release-pi.yml` - automatic Raspberry Pi Release packaging

## Security Before Publishing

Before pushing this repo online:

```bash
git status --short
git check-ignore -v .env config/home_assistant.json config/api_keys.json memory/long_term.json
```

Confirm that `.env` and local JSON state files are ignored. If a real token was ever committed to an old git history, rotate that token and publish from a fresh git history.

For a public repo, users should only copy `.env.example` to `.env` and add their own credentials there. Do not add real tokens directly to Python files, README examples, or tracked config files.

## Troubleshooting

If JARVIS hears you once and then stops, unplug and replug the USB mic, confirm it appears in `arecord -l`, and restart the assistant.

If you do not hear responses, check the default audio sink in Raspberry Pi OS audio settings and test output with:

```bash
speaker-test -t wav -c 2
```

If Home Assistant commands do not work, verify `HOME_ASSISTANT_URL`, verify the long-lived token, and make sure the entity names match your Home Assistant devices.

If the screen opens in the wrong orientation, fix display rotation in Raspberry Pi OS first, then restart the assistant.
