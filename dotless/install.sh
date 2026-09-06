#!/usr/bin/env bash
# One-time setup on Ubuntu 24.04 (run as root or with sudo). ~10 min on a small VPS.
set -euo pipefail
cd "$(dirname "$0")"
apt-get update -y
apt-get install -y ffmpeg rubberband-cli python3-venv python3-pip tmux
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
# CPU-only torch first, so demucs doesn't pull the multi-GB CUDA wheels
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
mkdir -p jobs
echo
echo "installed. next: cp .env.example .env  (add keys), then ./run.sh"
