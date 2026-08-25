# Requirements — RG35XX H Radio-Stream App

The status of each statement is marked **[VERIFIED]** (confirmed on the real device/environment),
**[TO VERIFY]** (assumed/pending confirmation on the device), or **[REQUIREMENT]**.

## 1. Installation and location

- **[VERIFIED]** The app is installed as the `/mnt/mmc/Roms/APPS/Radio.sh` launcher plus a support
  directory at `/mnt/mmc/Roms/APPS/radio/` (code, assets, bundled playback engine, playlist,
  persistent data).
- **[VERIFIED]** Firmware-menu icon convention: a 240x180 PNG at
  `/mnt/mmc/Roms/APPS/Imgs/Radio.png` (named `Imgs/<LauncherName>.png`), documented in
  `radio/README.md` §"Firmware menu integration".
- **[REQUIREMENT]** Nothing may be written outside `/mnt/mmc/Roms/APPS/radio/` (neither to `/` nor
  to system partitions). All persistence (favorites, recents, config, logs) lives on the SD card,
  within the app directory.

## 2. Target platform

- **[VERIFIED]** Hardware: RG35XX H, ARM64 (AArch64) CPU.
- **[VERIFIED]** Base OS: Ubuntu 22.04 (the device firmware image).
- **[VERIFIED]** Python 3.10.12 is available on the system.
- **[VERIFIED]** PySDL2 0.9.17 is available/targeted.
- **[VERIFIED]** Pillow 9.0.1 is available/targeted.
- **[VERIFIED]** Audio output is through internal ALSA (device speaker/headphone jack).
- **[VERIFIED]** 640x480 display: target logical resolution inherited from the project reference,
  used by this app's UI (`radio/ui/app.py`); it is not necessarily a measurement taken directly
  from the device's physical panel, but it is the target against which the interface is built and tested.
- **[VERIFIED]** `SDL_CreateWindow` with an explicit 640x480 size and the `SDL_WINDOW_FULLSCREEN`
  flag crashes on the device with `RuntimeError: SDL_CreateWindow failed:
  b"Could not initialize EGL"`. A known working reference app on the same device creates its window
  with `width=0, height=0` and flags `SDL_WINDOW_FULLSCREEN_DESKTOP | SDL_WINDOW_SHOWN`, and its
  launcher exports `PYSDL2_DLL_PATH=/usr/lib`; `radio/ui/app.py` and `Radio.sh` follow that same
  pattern (the logical 640x480 surface rendered with Pillow is scaled to the actual window size via
  `SDL_RenderSetLogicalSize`, rather than requesting a 640x480 window directly).
- **[VERIFIED]** Physical controls are read from `/dev/input/event1`.
- **[TO VERIFY]** Exact mapping of `EV_KEY` codes from `/dev/input/event1` to physical buttons
  (A/B/X/Y, D-pad, L1/R1/L2/R2, Start/Select, Menu/Power). It must be confirmed with `evtest` or
  equivalent on the device itself before the final mapping is fixed.
- **[TO VERIFY]** Whether other relevant `/dev/input/eventN` devices exist (for example, a second
  device for physical volume) that must also be read.
- **[TO VERIFY]** Actual free space on the user's SD card to host the bundled playback engine
  (AArch64 binary/runtime size + dependencies).
- **[VERIFIED]** The static AArch64 ffmpeg 9.0.1 binary (`radio/engine/ffmpeg-aarch64`) runs and
  decodes MP3/AAC/HLS on the device without depending on the system's broken libssl, provided
  `SSL_CERT_FILE` points to the bundled `cacert.pem` (see §3 and `radio/engine/README.md`).
- **[VERIFIED]** That same binary was compiled **without an ALSA output muxer**
  ("Requested output format alsa is not known."), so it cannot write audio directly to ALSA even
  though it decodes correctly. Actual playback uses two processes: the bundled ffmpeg decodes raw
  PCM (44.1 kHz stereo s16le) to stdout, and the system `/usr/bin/aplay` (which supports ALSA output)
  consumes that PCM on stdin with explicit format flags — see §3 and `radio/engine/README.md`.

## 3. Firmware players — why they are unusable [VERIFIED]

- Firmware `mpv`: fails to resolve OpenSSL 1.1 symbols (the binary is linked against an OpenSSL ABI
  not present or incompatible on the system) → cannot play anything over HTTPS.
- System `ffmpeg`/`ffplay`: fail due to a missing or broken Pango dependency → do not start.
- Vendor `ffplay` (included by the manufacturer): ELF mismatch (binary architecture/format incompatible
  with the device's actual userland) → does not run.
- Vendor `mplayer`: runs, but fails to open HTTPS streams as tested (no working TLS) → cannot open the
  vast majority of playlist streams (HTTPS/HLS).
- **Conclusion [REQUIREMENT]**: none of the preinstalled players is usable as-is. The app must bring
  its own playback engine, isolated from the system, compiled/packaged for AArch64 with its own working
  TLS stack.
- **[VERIFIED]** A static AArch64 (linux-arm64) `ffmpeg` 9.0.1 binary does run in the device userland
  (the ELF mismatch was specific to vendor `ffplay`, not the architecture/kernel in general) and correctly
  decodes MP3, AAC, and HLS streams, validating TLS certificates, provided `SSL_CERT_FILE` is pointed at
  the bundled `cacert.pem` instead of relying on the system's broken OpenSSL stack.
- **[VERIFIED]** That bundled binary does not include ffmpeg's `alsa` output muxer, so it cannot output
  audio directly through ALSA despite decoding correctly. The actual playback architecture uses two
  subprocesses chained by an operating-system pipe (never a shell pipeline): bundled ffmpeg decodes raw
  PCM on `pipe:1`, and the system `aplay` (which can communicate with ALSA and needs no TLS of its own)
  plays it by reading that PCM from stdin with explicit format flags matching the decoder output.

## 4. Playlist

- **[VERIFIED]** Format: Extended M3U (`#EXTM3U`, `#EXTINF`, grouping through `#EXTGRP`/the
  `group-title` attribute or an equivalent convention).
- **[VERIFIED]** 1,041 radio entries distributed across 24 groups/categories.
- **[VERIFIED]** Stream types present in the playlist:
  - MP3 over HTTPS
  - AAC over HTTPS
  - HLS (`.m3u8`)
  - Generic Icecast (mount points without a fixed extension, `icy-*` headers)
- **[TO VERIFY]** Whether all playlist URLs are live/accessible at time of use (Internet-radio
  playlists naturally experience station attrition; per-entry error handling rather than global blocking
  is recommended).
- **[TO VERIFY]** Character encoding of the M3U file (UTF-8 assumed) and whether optional metadata
  (per-entry logo, country, language) is present beyond name and group.

## 5. Functional requirements

- **[REQUIREMENT]** Bundled audio playback engine, isolated from the system (does not depend on
  firmware binaries/libraries except ALSA), compiled/packaged for AArch64, capable of playing MP3,
  AAC, and HLS (segmented, with downloading and assembly of segments) over HTTPS.
- **[REQUIREMENT]** Secure, current TLS stack included in the bundle (do not depend on the system's
  broken OpenSSL); it must validate certificates by default.
- **[REQUIREMENT]** In-house Extended M3U parser (or vendored library), tolerant of individual malformed
  entries without aborting the complete playlist load.
- **[REQUIREMENT]** Category navigation (the M3U's 24 groups).
- **[REQUIREMENT]** Search/filter by station name — optional, a UX improvement but not blocking for v1.
- **[REQUIREMENT]** Persistent favorites on the SD card (inside `radio/`), surviving app and device restarts.
- **[REQUIREMENT]** Persistent "recents" history on the SD card, with a reasonable entry limit.
- **[REQUIREMENT]** Clean playback-child-process lifecycle: startup, station changes without zombie/orphan
  processes, clean stop on exit, and no network-descriptor leaks when repeatedly changing stations.
- **[REQUIREMENT]** The UI must not block the screen or audio: the interface must remain responsive
  (input, navigation, screen refresh) while a stream connects/buffers/plays, without perceptible audio
  glitches caused by the UI thread.

## 6. Non-functional requirements

- **[REQUIREMENT]** Must not require package installation or writes outside the app directory (no
  `apt install`, nothing in `/usr`, `/etc`, the system home directory, etc.).
- **[REQUIREMENT]** Reasonably fast startup on embedded hardware (indicative target; **[TO VERIFY]**
  the exact acceptable figure on the real device).
- **[REQUIREMENT]** Memory/CPU usage compatible with low-end embedded hardware — **[TO VERIFY]**
  specific RAM/CPU limits available after the rest of the firmware.
- **[REQUIREMENT]** Network-failure tolerance: reconnection or a clear error message without hanging
  the app when a station does not respond or drops out during playback.

## 7. Acceptance tests

- **[REQUIREMENT]** Actual playback test of at least one MP3-over-HTTPS station from the playlist,
  with audible ALSA audio.
- **[REQUIREMENT]** Actual playback test of at least one AAC-over-HTTPS station.
- **[REQUIREMENT]** Actual playback test of at least one HLS (`.m3u8`) stream with multiple segments,
  including a transition between segments without an audible interruption.
- **[REQUIREMENT]** Playback test of at least one generic Icecast stream.
- **[REQUIREMENT]** Rapid station-changing test (audio-process lifecycle stress) without process/
  descriptor leaks.
- **[REQUIREMENT]** Complete M3U parsing test (1,041 entries / 24 groups), verifying the correct count
  of loaded entries and groups.
- **[REQUIREMENT]** Persistence test: add a favorite, close the app, reopen it, and confirm the favorite
  remains present.
- **[TO VERIFY]** All "actual" playback tests must run on the physical device (they cannot be faithfully
  simulated in desktop development) due to the ABI/architecture differences described in section 3.

## 8. Out of scope (v1)

- Stream recording.
- Playlist editing from the UI (content curation is done outside the app).
- Equalizer / audio effects.
- Multi-language interface support (beyond text in one initial language).
