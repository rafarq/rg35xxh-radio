# RG35XX H Radio

![RG35XX H Radio logo](assets/Radio-transparent.png)

Internet radio application for the **Anbernic RG35XX H** console running
**MuOS**. Browse the included M3U playlist, play stations, and keep favorites,
recent stations, and configuration on the SD card.

## Installation on MuOS

Copy the project files to the SD card using this structure:

```text
/mnt/mmc/Roms/APPS/Radio.sh
/mnt/mmc/Roms/APPS/radio/
```

In other words, copy `Radio.sh` to `Roms/APPS/` and the complete `radio/`
directory to `Roms/APPS/radio/`. For the menu icon, copy the corresponding PNG
as `Roms/APPS/Imgs/Radio.png`. Launch the application from the MuOS APPS menu.

The installation package must also include the AArch64 audio engine at
`radio/engine/ffmpeg-aarch64`; that binary is not included in this repository.
See [Architecture](#architecture) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Controls

| Control | Action |
| --- | --- |
| D-pad | Move the cursor or selection |
| A | Open, select, or play; retry after an error |
| B | Go back; while playing, stop and go back |
| SELECT | Return to Home |
| X | Play from a list; pause or resume while listening |
| START | Add or remove favorites |
| L1 / R1 | Previous / next category |
| VOL− / VOL+ | Adjust ALSA mixer volume |
| MENU | Exit cleanly |

## Stations and playlists

The included playlist contains MP3 and AAC stations, HLS (`.m3u8`) streams,
and generic Icecast streams. Availability of a particular station depends on
its remote server.

You can add local Extended M3U playlists (`.m3u` or `.m3u8`) in:

```text
/mnt/mmc/Roms/APPS/radio/playlists/
```

For remote playlists, add one complete `http://` or `https://` URL per line in:

```text
/mnt/mmc/Roms/APPS/radio/playlists/playlist_urls.txt
```

Blank lines and comments beginning with `#` are allowed; other schemes are
ignored. Then, on the console, open **Settings → Playlists**, choose the local
file or URL, and confirm with **A**. The selection is saved in the application
configuration.

Remote downloads use normal HTTPS certificate and hostname verification and
are limited to 2 MiB. Each valid download is cached; if a later refresh fails,
that copy is used. Without a valid cache—or if a local playlist does not exist,
is empty, or is invalid—Radio safely falls back to the included playlist.

## Architecture

The interface is written in Python and uses SDL2/Pillow on the device. The M3U
parser, playlist management, application state, and persistence are separated
from the interface layer so they can be tested on a desktop system.

Playback connects a static `ffmpeg-aarch64` decoder to `/usr/bin/aplay`:
FFmpeg converts the stream to PCM, and `aplay` sends it to ALSA. The CA bundle
at `radio/assets/cacert.pem` is supplied to the decoder through
`SSL_CERT_FILE` to verify HTTPS without relying on the firmware TLS store.

The `radio/engine/ffmpeg-aarch64` binary is intentionally excluded from Git
and must be obtained separately and included in the installation artifact.
When distributing it, the licensing and source-code availability obligations
for the specific build must be met; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[radio/engine/README.md](radio/engine/README.md).

## Development and testing

Python 3.10 or later is required. From the repository root:

```sh
uv run --with pytest python -m pytest -q
uv run python -m radio.smoke
```

The full interface requires the target hardware/firmware, PySDL2, Pillow,
ALSA, `/dev/input/event1`, and the external FFmpeg binary. The tests and smoke
test do not require SDL2 or a display.

## Security limitations

Radio accepts only HTTP(S) playlist URLs, retains HTTPS verification, and does
not execute playlists or URLs through a shell. M3U entries are treated as
untrusted data, and malformed entries are skipped. Cache and persistence stay
within the application tree on the SD card. These measures do not make
third-party streams trustworthy: use only playlists and stations you trust.

Vulnerabilities should be reported responsibly as described in
[SECURITY.md](SECURITY.md).

## Credits / Author

Rafael Roa

Technical Architect & CTO building at the intersection of architecture, code,
and AI.

- Website: https://rafarq.com
- GitHub: https://github.com/rafarq
- LinkedIn: https://www.linkedin.com/in/rafaroa
- Instagram: https://www.instagram.com/r4f4r04
- Threads: https://www.threads.net/@r4f4r04
- Mastodon: https://mastodon.cloud/@rafarq

License: [MIT](LICENSE).
