# radio

Python 3.10 app for the RG35XX H that browses and plays the bundled internet
radio playlist. See `PLAN.md` for the implementation plan and
`REQUISITOS.md` for verified/pending requirements.

## Layout

- `main.py` — entrypoint (`python3 -m radio.main`, invoked by `Radio.sh`).
  Resolves every path (playlist, data dir, engine binary, CA bundle) relative
  to this package's own directory, never the process's working directory.
- `playlist/` — Extended M3U parser (`parser.py`) and station model /
  stream-type classification (`model.py`). Tolerant of malformed entries: bad
  ones are skipped and recorded in `ParseResult.errors`, parsing never
  aborts.
- `storage.py` — `DataStore`: atomic JSON persistence for favorites, recents
  (deduplicated, capped at 20) and config, always written under the given
  data directory (never outside it).
- `playback/controller.py` — `PlayerController`: runs a *pair* of
  subprocesses per station, both launched as argv lists (never
  `shell=True`, never a shell pipeline string). The bundled
  `radio/engine/ffmpeg-aarch64` decodes the URL to raw PCM (44.1kHz stereo
  signed 16-bit little-endian) on stdout — it was built without an ALSA
  output muxer, so it can't write to ALSA itself — and the system's
  `/usr/bin/aplay` reads that PCM from stdin with explicit matching raw
  format flags and plays it over ALSA. The two are wired together via an OS
  pipe passed directly to `Popen`'s `stdin`/`stdout`. `SSL_CERT_FILE` is set
  to the bundled `radio/assets/cacert.pem` CA bundle for the decoder only
  (TLS verification stays on, just against a working root store; `aplay`
  never touches the network). Both subprocess lifecycles are cleaned up
  together via `terminate()` → timeout → `kill()` → `wait()`, so neither
  leaks as a zombie or holds pipe/socket fds open.
- `audio/volume.py` — `SystemVolume`: adapter over the real ALSA mixer via
  `/usr/bin/amixer`, targeting the `digital volume` simple control (raw range
  0-63 on this device, where 63 == 100%). Reads the current percentage with
  `amixer sget "digital volume"` and sets it with
  `amixer -q sset "digital volume" "NN%"`, always as argv lists (never
  `shell=True`). Every method degrades gracefully — a missing binary, missing
  control or unparsable output logs a warning and returns `None`/`False`
  instead of raising, so it can't crash the UI loop.
- `input/` — reads raw `input_event` structs from `/dev/input/event1` and
  normalizes them into button/axis `ControlEvent`s (`codes.py`, `reader.py`);
  see the confirmed key-code mapping in `input/codes.py`.
- `app/state.py` — `RadioApp`: the desktop-testable navigation/state
  machine. Owns screens, selection, playback wiring and persistence, with no
  SDL2/Pillow import so it's exercised directly by unit tests. On startup, if
  a `SystemVolume` was supplied and the hardware volume is readable, the
  displayed/persisted volume is synchronized from the real mixer instead of
  trusting the last value saved in `config.json`.
- `ui/` — the SDL2 + Pillow presentation layer (`app.py` runs the fullscreen
  640x480 frame loop; `render.py` draws each screen). SDL2/Pillow are only
  imported inside `ui.app.run`, so importing `radio.ui.app` and running the
  test suite never requires a display or those packages.
- `smoke.py` — CLI smoke test: parses `radio/data/playlist.m3u` and checks
  the parsed counts match the expected 1041 entries / 24 groups.
- `data/playlist.m3u` — the real playlist (1041 entries, 24 groups).
- `playlists/` — user-shareable playlist sources. Drop local `.m3u` or
  `.m3u8` files directly in `radio/playlists/`, or add remote playlist URLs
  to `radio/playlists/playlist_urls.txt` (one per line).
- `assets/cacert.pem` — the bundled CA bundle (a copy of `certifi`'s
  `cacert.pem`), used for `SSL_CERT_FILE`.
- `engine/` — expects the packaged `ffmpeg-aarch64` static binary at
  release/install time; see `radio/engine/README.md`. Not committed to this
  repository.
- `vendor/` — placeholder for vendorized Python dependencies not available
  on the device, to avoid `pip install` at runtime.

## Screens and controls

The launch screen is a visual Home dashboard with three icon cards:
Favorites, Categories (24 playlist groups) and Recents. Select a card and
press `A` to enter it. Categories leads to Stations and then Now Playing.

Button semantics (`app/state.py`):

- `A` — open the selected Home card / enter a category / play the selected
  station / retry after an error on the now-playing screen.
- `B` — always go back one screen; on now-playing, also stops playback. It is
  a safe no-op on Home.
- `SELECT` — return directly to the Home dashboard.
- `X` — starts the selected station from a station list; while listening,
  pauses or resumes the current stream. It is not a section shortcut.
- `START` — toggle favorite for the selected/now-playing station.
- Dedicated `VOL−` / `VOL+` buttons — adjust the real hardware mixer from
  every app screen. Radio uses the audible analog output `numid=3`
  (`lineout volume`, raw range 0–31) as the authoritative hardware and
  on-screen percentage. `numid=2` is locked by this firmware while the app
  is running and is deliberately not written.
- `L1`/`R1` — previous/next category.
- `DY` / `DX` (D-pad) — move the current list cursor; on Home either axis
  moves between the three icon cards. The D-pad does not change volume.
- `MENU` — request a clean exit (stops playback first).

Physical button/axis codes are read from `/dev/input/event1` and mapped in
`input/codes.py`.

## Adding and sharing playlists

On the SD card, local playlist files belong at the exact path
`App/radio/playlists/` and the remote URL list belongs at the exact path
`App/radio/playlists/playlist_urls.txt`. Copy a `.m3u` or `.m3u8` file into
that folder, then choose **Settings → Playlists** and select it. Filename
matching is case-insensitive.

For a remote playlist, edit `App/radio/playlists/playlist_urls.txt`: use one
complete `http://` or `https://` URL per line. Blank lines and `#` comments
are allowed. Only HTTP(S) is supported; other URL schemes are ignored. HTTPS
uses normal certificate and hostname verification, and a download is limited
to 2 MiB.

Radio caches each successful remote playlist. If it cannot refresh it later,
it uses the cached copy; if no valid cache exists (or a local list is missing,
empty, or malformed), it switches safely to the bundled playlist. To share
your setup, copy the whole `App` folder to the other device, then they can
select the copied source from Settings.

## Running

From the repository root, with a Python 3.10+ interpreter:

```
uv run --with pytest python -m pytest -q   # run the test suite
uv run python -m radio.smoke               # parse the real playlist, print counts
```

`pyproject.toml` sets `pythonpath = ["."]` so `import radio` resolves
without installing the package.

Running the full UI (`python3 -m radio.main` / `Radio.sh`) requires PySDL2,
Pillow, `/dev/input/event1`, ALSA and the bundled `engine/ffmpeg-aarch64`
binary, and is intended for the RG35XX H device itself — the test suite and
`smoke.py` never import SDL2/Pillow, so they run on a desktop without a
display.

## Firmware menu integration

For the app to show up with an icon in the RG35XX H firmware's menu, install
a PNG named to match the launcher script at:

```
/mnt/mmc/Roms/APPS/Imgs/Radio.png    # 240x180
```

The `Imgs/<LauncherName>.png` convention and the 240x180 size are the
firmware's menu-icon requirements, independent of this app's own 640x480 UI
resolution (`ui/render.py`). The icon is not part of `radio/`; it installs
under `Roms/APPS/Imgs/`.

## Crash log

`Radio.sh` redirects the app's stdout/stderr to `radio/log.txt` (appended,
not truncated) so failures on-device — where the firmware menu gives no
visible console — can be diagnosed after the fact by pulling the SD card or
mounting it over USB.
