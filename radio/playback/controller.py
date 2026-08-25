"""Isolated wrapper around the bundled ffmpeg-aarch64 decoder + system aplay.

Design constraints driven by REQUISITOS.md §3/§5/§6:

* The system's own ffmpeg/mpv are unusable (broken OpenSSL/Pango/ELF
  mismatch), so decoding only ever shells out to the bundled
  ``radio/engine/ffmpeg-aarch64`` static binary.
* That bundled ffmpeg build (9.0.1) was compiled without an ALSA *output*
  muxer ("Requested output format alsa is not known."), even though its
  demuxers/decoders and TLS stack work fine. So it cannot write directly to
  ALSA. Instead it decodes to raw PCM (44.1kHz stereo signed 16-bit
  little-endian) on stdout, and the system's ``/usr/bin/aplay`` — which
  *does* support ALSA output and needs no TLS/demuxing of its own — consumes
  that PCM on stdin with matching explicit raw-format flags.
* Both commands are always built as argument lists and launched with
  ``shell=False`` (the ``subprocess`` default), and the two processes are
  wired together via an OS pipe passed directly as ``stdin``/``stdout`` to
  ``Popen`` — never a shell pipeline string — to avoid command injection via
  station URLs pulled from the playlist.
* TLS verification stays on for the decoder; instead of trusting the
  device's broken system OpenSSL, ``SSL_CERT_FILE`` is pointed at the
  bundled ``radio/assets/cacert.pem`` CA bundle so certificate validation
  keeps working against a known-good root store. ``aplay`` never touches the
  network, so it doesn't need this.
* ``stop()`` always attempts a clean ``terminate()`` on both processes first
  and only escalates to ``kill()`` after a timeout, and always ``wait()``s
  for both, so channel-hopping never leaves orphaned processes (zombies) or
  leaked pipe/socket file descriptors behind.
"""

from __future__ import annotations

import enum
import os
import signal
import subprocess
from pathlib import Path
from typing import List, Optional

PCM_SAMPLE_RATE = "44100"
PCM_CHANNELS = "2"
PCM_FORMAT = "S16_LE"


class PlaybackState(str, enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class PlaybackError(RuntimeError):
    pass


class PlayerController:
    """Runs a decode (ffmpeg) + output (aplay) subprocess pair for one URL at a time."""

    def __init__(
        self,
        engine_path: "str | Path",
        cacert_path: "str | Path",
        alsa_device: str = "default",
        player_path: "str | Path" = "/usr/bin/aplay",
        terminate_timeout: float = 5.0,
        subprocess_module=subprocess,
    ):
        self.engine_path = Path(engine_path)
        self.cacert_path = Path(cacert_path)
        self.alsa_device = alsa_device
        self.player_path = Path(player_path)
        self.terminate_timeout = terminate_timeout
        self._subprocess = subprocess_module

        self._decoder_process: Optional[subprocess.Popen] = None
        self._player_process: Optional[subprocess.Popen] = None
        self._state: PlaybackState = PlaybackState.IDLE
        self._current_url: Optional[str] = None

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def current_url(self) -> Optional[str]:
        return self._current_url

    def build_decoder_command(self, url: str) -> List[str]:
        """Argv for the bundled ffmpeg: decode ``url`` to raw PCM on stdout.

        Returned as a list (never a shell string) so subprocess launches it
        directly without shell interpretation of the URL.
        """
        return [
            str(self.engine_path),
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            url,
            "-vn",
            "-f",
            "s16le",
            "-ar",
            PCM_SAMPLE_RATE,
            "-ac",
            PCM_CHANNELS,
            "pipe:1",
        ]

    def build_player_command(self) -> List[str]:
        """Argv for the system aplay: consume raw PCM from stdin over ALSA."""
        return [
            str(self.player_path),
            "-q",
            "-t",
            "raw",
            "-f",
            PCM_FORMAT,
            "-r",
            PCM_SAMPLE_RATE,
            "-c",
            PCM_CHANNELS,
            "-D",
            self.alsa_device,
            "-",
        ]

    def _build_decoder_env(self) -> dict:
        env = dict(os.environ)
        env["SSL_CERT_FILE"] = str(self.cacert_path)
        return env

    def start(self, url: str) -> None:
        """Stop any current playback and start a new decoder+player pair for url."""
        if self._decoder_process is not None or self._player_process is not None:
            self.stop()

        decoder_command = self.build_decoder_command(url)
        try:
            decoder_process = self._subprocess.Popen(
                decoder_command,
                shell=False,
                stdin=self._subprocess.DEVNULL,
                stdout=self._subprocess.PIPE,
                stderr=self._subprocess.PIPE,
                env=self._build_decoder_env(),
            )
        except OSError as exc:
            self._state = PlaybackState.ERROR
            self._current_url = None
            raise PlaybackError(f"failed to launch decoder engine: {exc}") from exc

        player_command = self.build_player_command()
        try:
            player_process = self._subprocess.Popen(
                player_command,
                shell=False,
                stdin=decoder_process.stdout,
                stdout=self._subprocess.DEVNULL,
                stderr=self._subprocess.PIPE,
            )
        except OSError as exc:
            self._terminate_process(decoder_process)
            if decoder_process.stdout is not None:
                decoder_process.stdout.close()
            self._state = PlaybackState.ERROR
            self._current_url = None
            raise PlaybackError(f"failed to launch aplay output: {exc}") from exc

        # The parent's copy of the decoder's stdout fd must be closed once
        # it's been handed to the player's stdin, or the player will never
        # see EOF (and the decoder will never see SIGPIPE) when either side
        # exits, since the parent would still be holding the pipe open.
        if decoder_process.stdout is not None:
            decoder_process.stdout.close()

        self._decoder_process = decoder_process
        self._player_process = player_process
        self._current_url = url
        self._state = PlaybackState.STARTING

    def poll(self) -> PlaybackState:
        """Non-blocking check of both subprocesses' liveness; updates and returns state."""
        if self._decoder_process is None or self._player_process is None:
            return self._state

        decoder_rc = self._decoder_process.poll()
        player_rc = self._player_process.poll()

        if decoder_rc is None and player_rc is None:
            if self._state == PlaybackState.STARTING:
                self._state = PlaybackState.PLAYING
            return self._state

        failed = (decoder_rc is not None and decoder_rc != 0) or (
            player_rc is not None and player_rc != 0
        )
        if failed:
            self._state = PlaybackState.ERROR
            self._cleanup(kill=True)
            return self._state

        if decoder_rc == 0 and player_rc == 0:
            self._state = PlaybackState.STOPPED
            self._cleanup(kill=False)
            return self._state

        # Exactly one of the two exited cleanly (0); let the other finish
        # draining/tearing down before deciding the final state.
        return self._state

    def _send_signal(self, process, sig: int) -> None:
        """Signal ``process`` only if it is still running (avoids ESRCH-style races)."""
        if process is not None and process.poll() is None:
            process.send_signal(sig)

    def pause(self) -> None:
        """Suspend both subprocesses in place (SIGSTOP) without tearing down the stream."""
        if self._state != PlaybackState.PLAYING:
            raise PlaybackError(f"cannot pause from state {self._state}")
        if self._decoder_process is None or self._player_process is None:
            raise PlaybackError("no active playback processes to pause")
        self._send_signal(self._decoder_process, signal.SIGSTOP)
        self._send_signal(self._player_process, signal.SIGSTOP)
        self._state = PlaybackState.PAUSED

    def resume(self) -> None:
        """Resume both subprocesses (SIGCONT) after a prior pause()."""
        if self._state != PlaybackState.PAUSED:
            raise PlaybackError(f"cannot resume from state {self._state}")
        if self._decoder_process is None or self._player_process is None:
            raise PlaybackError("no active playback processes to resume")
        self._send_signal(self._decoder_process, signal.SIGCONT)
        self._send_signal(self._player_process, signal.SIGCONT)
        self._state = PlaybackState.PLAYING

    def toggle_pause(self) -> None:
        """pause() if currently playing, resume() if currently paused."""
        if self._state == PlaybackState.PLAYING:
            self.pause()
        elif self._state == PlaybackState.PAUSED:
            self.resume()
        else:
            raise PlaybackError(f"cannot toggle pause from state {self._state}")

    def _terminate_process(self, process) -> None:
        if process.poll() is not None:
            # Already exited; still reap it so it doesn't linger as a zombie.
            process.wait()
            return

        process.terminate()
        try:
            process.wait(timeout=self.terminate_timeout)
        except self._subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=self.terminate_timeout)

    def _cleanup(self, kill: bool) -> None:
        decoder_process = self._decoder_process
        player_process = self._player_process
        self._decoder_process = None
        self._player_process = None
        self._current_url = None

        for process in (decoder_process, player_process):
            if process is None:
                continue
            if kill:
                self._terminate_process(process)
            else:
                process.wait()

    def stop(self) -> None:
        """Terminate both subprocesses, escalating to kill() on timeout, and reap them."""
        if self._decoder_process is None and self._player_process is None:
            self._state = PlaybackState.STOPPED
            return

        if self._state == PlaybackState.PAUSED:
            # A SIGSTOPped process won't act on SIGTERM until continued, so
            # resume it first or terminate() below would just hang until the
            # kill() timeout escalation.
            self._send_signal(self._decoder_process, signal.SIGCONT)
            self._send_signal(self._player_process, signal.SIGCONT)

        self._cleanup(kill=True)
        self._state = PlaybackState.STOPPED

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass
