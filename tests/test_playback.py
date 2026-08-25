from pathlib import Path

import pytest

from radio.playback.controller import PlaybackError, PlaybackState, PlayerController

ENGINE_PATH = Path("/opt/radio/engine/ffmpeg-aarch64")
CACERT_PATH = Path("/opt/radio/assets/cacert.pem")
PLAYER_PATH = Path("/usr/bin/aplay")


class FakeTimeoutExpired(Exception):
    def __init__(self, timeout=None):
        self.timeout = timeout


class FakePipe:
    """Stand-in for the OS pipe object subprocess.Popen puts on .stdout."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeProcess:
    """Stand-in for subprocess.Popen used to test lifecycle without real binaries."""

    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.terminated = False
        self.killed = False
        self.wait_calls = 0
        self._returncode = None
        # Test hook: how many wait() calls terminate() needs before "dying".
        self.dies_after_waits = 1
        self.hangs_forever = False
        self.stdout = FakePipe() if kwargs.get("stdout") == FakeSubprocessModule.PIPE else None

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self._returncode = -9

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.hangs_forever and not self.killed:
            raise FakeTimeoutExpired(timeout)
        if self.terminated and self.wait_calls >= self.dies_after_waits:
            self._returncode = 0
        return self._returncode


class FakeSubprocessModule:
    DEVNULL = -3
    PIPE = -1
    TimeoutExpired = FakeTimeoutExpired

    def __init__(self):
        self.processes = []
        self.next_should_raise = None
        # Test hook: fail only the Nth Popen call (1-indexed), if set.
        self.raise_on_call = None
        self._call_count = 0

    def Popen(self, args, **kwargs):
        self._call_count += 1
        if self.next_should_raise is not None:
            if self.raise_on_call is None or self._call_count == self.raise_on_call:
                raise self.next_should_raise
        process = FakeProcess(args, **kwargs)
        self.processes.append(process)
        return process


@pytest.fixture
def fake_subprocess():
    return FakeSubprocessModule()


@pytest.fixture
def controller(fake_subprocess):
    return PlayerController(
        engine_path=ENGINE_PATH,
        cacert_path=CACERT_PATH,
        alsa_device="default",
        player_path=PLAYER_PATH,
        terminate_timeout=0.01,
        subprocess_module=fake_subprocess,
    )


# -- command construction -----------------------------------------------------


def test_build_decoder_command_is_argv_list_not_shell_string():
    controller = PlayerController(ENGINE_PATH, CACERT_PATH, player_path=PLAYER_PATH)
    command = controller.build_decoder_command("https://example.com/stream.mp3")
    assert isinstance(command, list)
    assert all(isinstance(part, str) for part in command)
    assert command[0] == str(ENGINE_PATH)
    assert "https://example.com/stream.mp3" in command


def test_build_decoder_command_outputs_raw_pcm_to_stdout_not_alsa():
    controller = PlayerController(ENGINE_PATH, CACERT_PATH, player_path=PLAYER_PATH)
    command = controller.build_decoder_command("https://example.com/stream.mp3")
    assert "alsa" not in command
    assert command[command.index("-f") + 1] == "s16le"
    assert command[command.index("-ar") + 1] == "44100"
    assert command[command.index("-ac") + 1] == "2"
    assert command[-1] == "pipe:1"


def test_build_player_command_is_argv_list_targeting_aplay():
    controller = PlayerController(
        ENGINE_PATH, CACERT_PATH, alsa_device="hw:0,0", player_path=PLAYER_PATH
    )
    command = controller.build_player_command()
    assert isinstance(command, list)
    assert all(isinstance(part, str) for part in command)
    assert command[0] == str(PLAYER_PATH)
    assert command[command.index("-t") + 1] == "raw"
    assert command[command.index("-f") + 1] == "S16_LE"
    assert command[command.index("-r") + 1] == "44100"
    assert command[command.index("-c") + 1] == "2"
    assert command[command.index("-D") + 1] == "hw:0,0"


# -- process wiring ------------------------------------------------------------


def test_start_launches_two_processes_without_shell(controller, fake_subprocess):
    controller.start("https://example.com/stream.mp3")
    assert len(fake_subprocess.processes) == 2
    decoder_process, player_process = fake_subprocess.processes
    assert decoder_process.kwargs["shell"] is False
    assert player_process.kwargs["shell"] is False
    assert isinstance(decoder_process.args, list)
    assert isinstance(player_process.args, list)
    assert decoder_process.args[0] == str(ENGINE_PATH)
    assert player_process.args[0] == str(PLAYER_PATH)


def test_start_wires_decoder_stdout_into_player_stdin(controller, fake_subprocess):
    controller.start("https://example.com/stream.mp3")
    decoder_process, player_process = fake_subprocess.processes
    assert decoder_process.kwargs["stdout"] == fake_subprocess.PIPE
    assert player_process.kwargs["stdin"] is decoder_process.stdout


def test_start_closes_parents_copy_of_decoder_stdout_pipe(controller, fake_subprocess):
    controller.start("https://example.com/stream.mp3")
    decoder_process, _player_process = fake_subprocess.processes
    assert decoder_process.stdout.closed is True


def test_start_sets_ssl_cert_file_on_decoder_only(controller, fake_subprocess):
    controller.start("https://example.com/stream.mp3")
    decoder_process, player_process = fake_subprocess.processes
    assert decoder_process.kwargs["env"]["SSL_CERT_FILE"] == str(CACERT_PATH)
    assert "env" not in player_process.kwargs or player_process.kwargs.get("env") is None


def test_start_sends_player_stdout_and_stderr_away_from_decoder(controller, fake_subprocess):
    controller.start("https://example.com/stream.mp3")
    decoder_process, player_process = fake_subprocess.processes
    assert decoder_process.kwargs["stdin"] == fake_subprocess.DEVNULL
    assert decoder_process.kwargs["stderr"] == fake_subprocess.PIPE
    assert player_process.kwargs["stdout"] == fake_subprocess.DEVNULL
    assert player_process.kwargs["stderr"] == fake_subprocess.PIPE


# -- state transitions ----------------------------------------------------------


def test_state_transitions_starting_then_playing(controller):
    controller.start("https://example.com/a.mp3")
    assert controller.state == PlaybackState.STARTING
    assert controller.poll() == PlaybackState.PLAYING
    assert controller.state == PlaybackState.PLAYING


def test_decoder_error_exit_sets_error_state_and_cleans_up_player(controller, fake_subprocess):
    controller.start("https://example.com/a.mp3")
    decoder_process, player_process = fake_subprocess.processes
    decoder_process._returncode = 1
    assert controller.poll() == PlaybackState.ERROR
    assert controller.current_url is None
    # The still-running player must be terminated too, not left dangling.
    assert player_process.terminated is True


def test_player_error_exit_sets_error_state_and_cleans_up_decoder(controller, fake_subprocess):
    controller.start("https://example.com/a.mp3")
    decoder_process, player_process = fake_subprocess.processes
    player_process._returncode = 1
    assert controller.poll() == PlaybackState.ERROR
    assert controller.current_url is None
    assert decoder_process.terminated is True


def test_both_clean_exit_sets_stopped_state(controller, fake_subprocess):
    controller.start("https://example.com/a.mp3")
    decoder_process, player_process = fake_subprocess.processes
    decoder_process._returncode = 0
    player_process._returncode = 0
    assert controller.poll() == PlaybackState.STOPPED
    assert controller.current_url is None


def test_only_decoder_exited_cleanly_keeps_state_until_player_finishes(controller, fake_subprocess):
    controller.start("https://example.com/a.mp3")
    decoder_process, player_process = fake_subprocess.processes
    controller.poll()  # STARTING -> PLAYING
    decoder_process._returncode = 0
    # Player is still draining the pipe; not stopped yet.
    assert controller.poll() == PlaybackState.PLAYING
    player_process._returncode = 0
    assert controller.poll() == PlaybackState.STOPPED


# -- stop / cleanup --------------------------------------------------------------


def test_stop_terminates_both_processes_cleanly(controller, fake_subprocess):
    controller.start("https://example.com/a.mp3")
    decoder_process, player_process = fake_subprocess.processes
    controller.stop()
    assert decoder_process.terminated is True
    assert decoder_process.killed is False
    assert player_process.terminated is True
    assert player_process.killed is False
    assert controller.state == PlaybackState.STOPPED
    assert controller.current_url is None


def test_stop_waits_on_both_processes_to_avoid_zombies(controller, fake_subprocess):
    controller.start("https://example.com/a.mp3")
    decoder_process, player_process = fake_subprocess.processes
    controller.stop()
    assert decoder_process.wait_calls >= 1
    assert player_process.wait_calls >= 1


def test_stop_escalates_to_kill_on_timeout_for_both(controller, fake_subprocess):
    controller.start("https://example.com/a.mp3")
    decoder_process, player_process = fake_subprocess.processes
    decoder_process.hangs_forever = True
    player_process.hangs_forever = True
    controller.stop()
    assert decoder_process.killed is True
    assert player_process.killed is True
    assert controller.state == PlaybackState.STOPPED


def test_stop_kills_only_the_hung_process(controller, fake_subprocess):
    controller.start("https://example.com/a.mp3")
    decoder_process, player_process = fake_subprocess.processes
    decoder_process.hangs_forever = True
    controller.stop()
    assert decoder_process.killed is True
    assert player_process.killed is False
    assert player_process.terminated is True


def test_start_while_playing_stops_previous_pair_first(controller, fake_subprocess):
    controller.start("https://example.com/first.mp3")
    first_decoder, first_player = fake_subprocess.processes
    controller.start("https://example.com/second.mp3")
    assert first_decoder.terminated is True
    assert first_player.terminated is True
    assert controller.current_url == "https://example.com/second.mp3"
    assert len(fake_subprocess.processes) == 4


def test_stop_when_never_started_is_a_no_op(controller):
    controller.stop()
    assert controller.state == PlaybackState.STOPPED


# -- launch failures --------------------------------------------------------------


def test_decoder_popen_failure_raises_playback_error(controller, fake_subprocess):
    fake_subprocess.next_should_raise = OSError("no such file")
    with pytest.raises(PlaybackError):
        controller.start("https://example.com/a.mp3")
    assert controller.state == PlaybackState.ERROR
    assert controller.current_url is None
    assert len(fake_subprocess.processes) == 0


def test_player_popen_failure_cleans_up_already_started_decoder(controller, fake_subprocess):
    fake_subprocess.next_should_raise = OSError("aplay not found")
    fake_subprocess.raise_on_call = 2
    with pytest.raises(PlaybackError):
        controller.start("https://example.com/a.mp3")
    assert controller.state == PlaybackState.ERROR
    assert controller.current_url is None
    decoder_process = fake_subprocess.processes[0]
    assert decoder_process.terminated is True
    assert decoder_process.stdout.closed is True
