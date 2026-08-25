# engine

This directory must contain the packaged audio engine binary at install
time:

```
radio/engine/ffmpeg-aarch64
```

## Required release artifact

`ffmpeg-aarch64` is a **static** `ffmpeg` build for AArch64 (linux-arm64),
not committed to this repository (see `.gitignore`). It must be added to
the packaged release/SD-card image separately, alongside its execute bit
(FAT32 doesn't always preserve it across a copy, so `Radio.sh` also
re-asserts `chmod +x` on every launch as a fallback).

Verified on the RG35XX H device (REQUISITOS.md §3, PLAN.md §0): a static
AArch64 build of ffmpeg 9.0.1 runs correctly on the device's userland (the
vendor `ffplay`'s ELF mismatch was specific to that binary, not the
architecture/kernel in general) and decodes MP3, AAC and HLS streams from
the playlist, including TLS certificate validation — see the CA bundle
requirement below.

**Verified on-device:** this bundled ffmpeg build was compiled *without* an
ALSA **output** muxer ("Requested output format alsa is not known."), even
though its demuxers/decoders/TLS stack are fine. So it cannot write audio
directly to ALSA itself. `playback/controller.py` therefore runs it purely
as a decoder, piping raw PCM to a second process — the system's
`/usr/bin/aplay`, which does support ALSA output and needs no TLS/demuxing
of its own — over an OS pipe wired directly between the two `Popen` calls
(never a shell pipeline string):

```
ffmpeg-aarch64 -nostdin -loglevel error -i <url> -vn -f s16le -ar 44100 -ac 2 pipe:1
  | (OS pipe, wired via subprocess Popen stdin/stdout, not a shell)
aplay -q -t raw -f S16_LE -r 44100 -c 2 -D <device> -
```

Both subprocess lifecycles are managed independently: the parent's copy of
the decoder's stdout pipe fd is closed right after the player process
inherits it (so EOF/SIGPIPE propagate correctly between the two), and both
processes are terminated → (timeout) → killed → waited-on together on
channel change, clean exit, or launch failure, so neither can leak as a
zombie or hold an open pipe/socket fd.

## Runtime TLS CA requirement

The system's OpenSSL on the device is broken/incompatible, so the bundled
ffmpeg must be pointed at a known-good CA bundle explicitly rather than
relying on the system root store. `PlayerController` sets the environment
variable `SSL_CERT_FILE` to `radio/assets/cacert.pem` (a copy of
`certifi`'s bundle) for every engine invocation. TLS verification stays on;
this only replaces which root store it verifies against.

Verified on-device: MP3/AAC decode over HTTPS and HLS decode both work with
`SSL_CERT_FILE` pointing at a valid CA bundle; without it, the bundled
ffmpeg cannot validate certificates against the system's OpenSSL install.

## Licensing

See `../../THIRD_PARTY_NOTICES.md` — packaging this binary carries FFmpeg's
license and source-availability obligations that must be satisfied
alongside the release, not just the binary itself.
