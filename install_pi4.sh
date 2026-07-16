#!/usr/bin/env bash
set -euo pipefail

if [ "$(uname -m)" != "aarch64" ]; then
  echo "WARNING: Raspberry Pi OS 64-bit is recommended (detected: $(uname -m))."
fi

sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip python3-pyqt6 python3-pyqt6.qtquick python3-numpy \
  qml6-module-qtquick \
  portaudio19-dev pulseaudio-utils pipewire-audio bluetooth bluez \
  libgl1 libegl1 libxcb-cursor0 \
  xserver-xorg xinit openbox dbus-x11 x11-xserver-utils \
  xserver-xorg-input-libinput xinput unclutter-xfixes

cd "$(dirname "$0")"
python3 -m venv --system-site-packages .venv
./.venv/bin/python -m pip install --upgrade pip wheel setuptools

# openWakeWord 0.6 declares tflite-runtime as mandatory on Linux, but that
# package has no Python 3.13 ARM64 wheel. Jarvis uses the supported ONNX path,
# so install the remaining requirements normally and openWakeWord without the
# unused TFLite dependency.
grep -v '^openwakeword==' requirements.txt > .requirements-pi.txt
./.venv/bin/pip install -r .requirements-pi.txt
./.venv/bin/pip install \
  'onnxruntime>=1.10,<2' 'tqdm>=4,<5' 'scipy>=1.3,<2' 'scikit-learn>=1,<2'
./.venv/bin/pip install --no-deps openwakeword==0.6.0
rm -f .requirements-pi.txt

# Download the pre-trained model once during setup. Runtime never needs to
# download a model and can therefore keep standby audio entirely local.
./.venv/bin/python -c "from openwakeword.utils import download_models; download_models(['hey_jarvis'])"

if [ ! -f .env ]; then
  cp .env.example .env
fi

chmod +x launch_assistant.sh start_jarvis_pi.sh configure_pi_display.sh assistantctl diagnose_pi_live.sh
./.venv/bin/python -m compileall -q omar_ai_core audio_check.py start_assistant.py
echo
echo "Installation complete. Next steps:"
echo "  1. Edit .env and add GEMINI_API_KEY."
echo "  2. Run: ./.venv/bin/python audio_check.py"
echo "  3. Run: ./launch_assistant.sh"
