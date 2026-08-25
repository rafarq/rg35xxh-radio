#!/bin/sh
# Launcher for the RG35XX H firmware menu.
#
# Expected install layout on the SD card (PLAN.md §1):
#   /mnt/mmc/Roms/APPS/Radio.sh
#   /mnt/mmc/Roms/APPS/radio/            (this script's sibling directory)
#
# Menu icon: the firmware menu picks up /mnt/mmc/Roms/APPS/Imgs/Radio.png
# (240x180) for this launcher's entry — see radio/README.md.
#
# FAT32 does not always preserve the executable bit on files copied via
# USB/card reader (PLAN.md Fase 5), so this script re-asserts execute
# permission on the bundled engine binary on every launch rather than
# assuming it survived the copy.
#
# PYSDL2_DLL_PATH=/usr/lib mirrors the known-good reference app on this
# same device: without it PySDL2 can fail to locate the system SDL2
# shared library before the frame loop ever gets a chance to create a
# window.
#
# All stdout/stderr is appended to radio/log.txt (writable, inside the
# app's own tree per REQUISITOS.md §1) so on-device crashes — which are
# otherwise invisible, since the firmware menu gives no console — are
# diagnosable after the fact. This script also brackets every run with
# its own timestamped launch/exit marker lines (in addition to, not
# instead of, the app's own per-stage logging from radio/log.py) so a
# stuck or silently-killed process is still visible as "launch marker
# with no matching exit marker" in the log.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR/radio"
ENGINE="$APP_DIR/engine/ffmpeg-aarch64"
LOG_FILE="$APP_DIR/log.txt"

if [ -f "$ENGINE" ]; then
    chmod +x "$ENGINE" 2>/dev/null || true
fi

export PYSDL2_DLL_PATH=/usr/lib

cd "$SCRIPT_DIR"

mkdir -p "$APP_DIR"
echo "===== Radio.sh launch marker: $(date -u +%Y-%m-%dT%H:%M:%SZ) =====" >>"$LOG_FILE"
{
    echo "runtime identity: $(id)"
    ls -l /dev/snd/controlC0 2>&1 || true
    raw_volume=$(/usr/bin/amixer cget numid=2 2>/dev/null | awk -F= '/: values=/{print $2; exit}')
    if [ -n "${raw_volume:-}" ] && /usr/bin/amixer -q cset numid=2 "$raw_volume" 2>/dev/null; then
        echo "mixer write preflight: ok (numid=2 raw=$raw_volume)"
    else
        echo "mixer write preflight: FAILED"
    fi
} >>"$LOG_FILE" 2>&1

set +e
python3 -m radio.main >>"$LOG_FILE" 2>&1
STATUS=$?
set -e

echo "===== Radio.sh exit marker: $(date -u +%Y-%m-%dT%H:%M:%SZ) status=$STATUS =====" >>"$LOG_FILE"

exit "$STATUS"
