# Implementation Plan — RG35XX H Radio-Stream App

This document is the technical plan. It contains no code or development-machine installation steps;
it describes what will be built, in what order, and how it will be validated on the real device.
Items marked **[TO VERIFY]** require confirmation on the RG35XX H before they can be considered
complete.

## 0. Key decision: bundled playback engine

Because none of the firmware media players is usable (see REQUISITOS.md §3), the project's core
requirement is to package an isolated AArch64 audio engine, with its own TLS stack, inside
`/mnt/mmc/Roms/APPS/radio/engine/`.

Candidates to evaluate, in pragmatic order of preference:

1. **Static `ffmpeg`/`ffprobe` binary for AArch64 (linux-arm64, static build)**, invoked as a
   subprocess through `ffmpeg -> PCM pipe -> ALSA` or directly with `ffmpeg`'s `alsa` output.
   Advantage: a static build includes its own OpenSSL/GnuTLS and demuxers (mp3, aac, hls), without
   relying on Pango (the system failure is in a *subtitle/UI filter* dependency, which is irrelevant
   for pure audio with no text overlay). It is the simplest option to isolate: one binary, no system
   libraries.
2. **Static `libmpv` or `mpv` for AArch64**, controlled through `python-mpv` (JSON IPC) instead of
   the broken firmware `mpv`. Advantage: better native HLS handling and reconnection. It requires
   verifying that the static build does not reintroduce the same OpenSSL 1.1 symbol issue as the
   firmware mpv (that is why the system `mpv` is rejected, not the concept of mpv).
3. Fallback: manual `mpg123`/`faad2`/HLS parser — rejected unless options 1 and 2 fail, due to the
   cost of reimplementing HLS demuxing.

**Working-plan choice:** start with option 1, static AArch64 ffmpeg as a subprocess, because it is
the lowest-risk path (one binary, formats already covered, its own TLS). Option 2 remains a documented
Plan B if real tests show that ffmpeg subprocess startup/latency is poor when changing stations quickly.

**[VERIFIED]** on the device: a static AArch64 ffmpeg 9.0.1 binary runs correctly (the ELF mismatch
was specific to the vendor `ffplay`, not the device architecture/kernel) and decodes real MP3, AAC,
and HLS streams from the playlist with active TLS verification, provided `SSL_CERT_FILE` points to the
bundled `cacert.pem` (see REQUISITOS.md §3).

**[VERIFIED]** that same binary was built without the `alsa` output muxer
("Requested output format alsa is not known."), so it cannot output audio directly through ALSA.
The final playback architecture uses **two** subprocesses per station, both launched as argument lists
(`shell=False`), never as a shell pipeline: the bundled ffmpeg decodes the URL to raw PCM (44.1 kHz
stereo s16le) on `pipe:1`, and the system `aplay` receives that PCM on stdin—with explicit format flags
that exactly match the decoder output—and plays it through ALSA. The two processes are connected by an
operating-system pipe passed directly to `Popen` (decoder `stdout` as player `stdin`), and their
lifecycles are managed together (`terminate()` → timeout → `kill()` → `wait()` for both) so that no
zombies or open pipes/sockets remain when changing stations.

## 1. Installed package structure

```
/mnt/mmc/Roms/APPS/Radio.sh              # launcher, invokes python3 on radio/main.py
/mnt/mmc/Roms/APPS/radio/
  main.py                                # entry point
  ui/                                    # SDL2 + Pillow layer
  playlist/                              # M3U parser, category model
  playback/                              # engine wrapper (ffmpeg subprocess), lifecycle
  input/                                 # reads /dev/input/event1, button mapping
  data/
    playlist.m3u                         # the 1,041 entries / 24 groups
    favorites.json                       # persistent
    recent.json                          # persistent
    config.json                          # user configuration (volume, last group, etc.)
  engine/
    ffmpeg-aarch64                       # bundled static binary
  vendor/                                # vendored Python dependencies if unavailable on the
                                          # system (avoid pip install on the device)
  assets/                                # icons, Pillow fonts
```

All writable files (favorites.json, recent.json, config.json, logs) live under `radio/data/` on the
SD card. Nothing outside this tree is touched.

## 2. Work phases

### Phase 1 — Foundations without a UI

- Extended M3U parser: reads `playlist.m3u`, produces a list of
  `(title, url, group, inferred_stream_type)` entries, and tolerates malformed lines (they are skipped
  with a log entry; parsing does not abort). Unit test against the real file with 1,041 entries / 24 groups.
- Data model for categories (24 groups) and favorites/recents (JSON read/write in `data/`).
- Playback wrapper: launches `ffmpeg-aarch64` (decoder, without ALSA muxer) and `aplay` (ALSA output)
  as a pair of subprocesses connected by an OS pipe; manages their stdout/stderr; exposes states
  (connecting/playing/error/stopped); and cleanly kills both processes (`terminate` + timeout + `kill`
  if they do not respond) when changing stations or exiting.
- Early decision point: **[TO VERIFY]** on the device, with the first three phases implemented but no UI
  (a minimal command-line script), confirm that it can: (a) run the static ffmpeg binary, (b) output
  audio through internal ALSA, and (c) correctly play at least one MP3-HTTPS URL, one AAC-HTTPS URL, and
  one HLS URL from the real playlist. This checkpoint must pass before investing in the UI layer.

### Phase 2 — Input and process lifecycle

- Read `/dev/input/event1` (through vendored `python-evdev` or direct parsing of the `input_event`
  structure with `struct`, to minimize external dependencies).
- **[TO VERIFY]** button mapping against real hardware before fixing constants.
- Non-blocking main loop: input reading, UI refresh, and audio-subprocess status polling must coexist in
  the same loop (or in separate threads with thread-safe queues) without a stream connection/buffer freezing
  screen redraws or interrupting audio already playing.
- Stress tests: repeatedly change stations (for example, 30 consecutive changes), checking that no orphaned
  `ffmpeg` processes or excess open socket descriptors remain (`/proc/<pid>/fd` of the main process before/after).

### Phase 3 — UI (PySDL2 + Pillow, logical 640x480)

640x480 is the target logical resolution inherited from the project reference (see REQUISITOS.md §2),
not necessarily a measurement taken from the device's physical panel; it is the size on which `radio/ui/`
is built and tested.

- Category screen (24 groups).
- Station-list screen within a group, with scrolling.
- "Now Playing" screen: station name, group, state (connecting/live/error), volume controls, favorite toggle.
- Favorites and recents screens.
- Name search/filter — **optional**, implemented after the preceding screens are validated on the device,
  using virtual-keyboard input with the D-pad or, if the device permits it, text through button combinations;
  if the UX cost is high for a controller without a keyboard, it can be reduced to an alphabetical-prefix
  filter (jump to a letter) as the more pragmatic default.
- All rendering is done in Pillow and uploaded to an SDL2 texture; the UI thread/loop never waits
  synchronously for a stream to connect (that wait lives in the playback wrapper, which is polled non-blockingly).

### Phase 4 — Persistence and startup UX

- Remember the last viewed category/station (`config.json`).
- Favorites and recents are already covered in Phase 1; here they are connected to the UI (favorite toggle,
  capped recents view, for example 20 entries, most-recent-first order).
- Per-entry network-error handling: if a URL fails, show a clear message and allow returning to the list
  without crashing the app; it must not halt global navigation.

### Phase 5 — Packaging and installation

- Generate `Radio.sh` (minimal shell wrapper that invokes `python3 radio/main.py`, with `PATH`/
  `LD_LIBRARY_PATH` pointing only to bundled resources if needed, so it does not interfere with the rest
  of the firmware).
- Verify that execute permissions for the `.sh` and `ffmpeg-aarch64` binary survive a typical copy to the
  SD card through USB/card reader (FAT32 does not always preserve the execute bit) — **[TO VERIFY]**; if
  not, `Radio.sh` itself must run `chmod +x` on the engine binary at first launch, since that is an allowed
  write within `radio/`.
- **[TO VERIFY]** the icon/name convention expected by the firmware menu launcher.

## 3. Pragmatic UX defaults

- Initial volume: medium (for example, 60%), adjustable with the left/right D-pad on the playback screen;
  persisted in `config.json`.
- During playback, use a reasonable connection timeout (for example, 8–10 s) before showing an error,
  rather than waiting indefinitely — exact value to be adjusted after real device network-latency testing
  **[TO VERIFY]**.
- Recents limited to the last ~20 stations played, without duplicates (playing an existing one again moves it
  to the top).
- Text search is a *nice-to-have*; alphabetical letter jumping (hold a button to cycle A→Z) is the default
  quick-filtering mechanism, better suited to a controller with no physical keyboard.
- No blocking loading screen: when entering "Now Playing," display the UI immediately with a "connecting…"
  state, and start audio as soon as the subprocess begins outputting PCM.

## 4. Acceptance test plan (real device)

All tests run on the physical RG35XX H, not in desktop development, due to the ABI differences described
in REQUISITOS.md §3.

1. Actual playback of an MP3-HTTPS station from the playlist, with audible audio.
2. Actual playback of an AAC-HTTPS station.
3. Actual playback of an HLS (`.m3u8`) stream, including at least one segment transition without a
   perceptible audible interruption.
4. Actual playback of a generic Icecast stream (no extension, `icy-*` headers).
5. Complete parsing of `playlist.m3u`: the count of 1,041 entries and 24 groups matches expectations.
6. Change stations 30 consecutive times without orphaned `ffmpeg` processes or descriptor leaks.
7. Favorite cycle: mark, exit the app, reopen it, and confirm the favorite persists.
8. Recents cycle: play 3 distinct stations; verify order and persistence after restarting the app.
9. Responsive UI during connection: while a station is "connecting," navigation (going back, moving through
   the list) continues to respond without perceptible blocking.
10. Down station/dead URL: the app shows an error and allows navigation to continue without hanging.

## 5. Risks and outstanding verification points (summary)

- Actual ability to run a static AArch64 ffmpeg binary in the device userland (vendor ffplay had an ELF
  mismatch; a generic static linux-arm64 build must be confirmed to run).
- Button-code mapping for `/dev/input/event1`.
- Preservation of execute permissions when copying to the SD card through FAT32.
- Exact firmware launcher-menu integration convention (icon, metadata).
- Actual RAM/CPU limits available to the app after the rest of the firmware.
- Acceptable startup time and connection latency on the actual network where the device is used.
