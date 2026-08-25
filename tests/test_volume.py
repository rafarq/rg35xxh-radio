from radio.audio.volume import SystemVolume, percent_to_raw, raw_to_percent

DIGITAL_RAW_MAX = 63
LINEOUT_RAW_MAX = 31


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeSubprocess:
    """Records the argv it was called with and returns a scripted result.

    ``results`` may be a single result reused for every call, or a list
    consumed in call order (so digital/lineout calls can be scripted to
    succeed/fail independently).
    """

    def __init__(self, result=None, results=None, exc=None):
        self.result = result or FakeCompletedProcess()
        self.results = results
        self.exc = exc
        self.calls = []
        self.kwargs = []

    def run(self, command, **kwargs):
        self.calls.append(command)
        self.kwargs.append(kwargs)
        if self.exc is not None:
            raise self.exc
        if self.results is not None:
            return self.results[len(self.calls) - 1]
        return self.result


CGET_OUTPUT = """numid=3,iface=MIXER,name='Playback Lineout Volume'
  ; type=INTEGER,access=rw---R--,values=1,min=0,max=31,step=1
  : values=18
  | dBscale-min=-43.50dB,step=1.50dB,mute=0
"""


def test_raw_to_percent_and_back_roundtrip_at_bounds():
    assert raw_to_percent(0) == 0
    assert raw_to_percent(63) == 100
    assert percent_to_raw(0) == 0
    assert percent_to_raw(100) == 63


def test_percent_to_raw_rounds_to_nearest():
    assert percent_to_raw(55) == round(55 * 63 / 100)  # 35
    assert percent_to_raw(50) == round(50 * 63 / 100)  # 32


def test_raw_to_percent_clamps_out_of_range():
    assert raw_to_percent(-5) == 0
    assert raw_to_percent(999) == 100


def test_lineout_raw_to_percent_and_back_roundtrip_at_bounds():
    assert raw_to_percent(0, LINEOUT_RAW_MAX) == 0
    assert raw_to_percent(31, LINEOUT_RAW_MAX) == 100
    assert percent_to_raw(0, LINEOUT_RAW_MAX) == 0
    assert percent_to_raw(100, LINEOUT_RAW_MAX) == 31


def test_lineout_percent_to_raw_rounds_to_nearest():
    assert percent_to_raw(55, LINEOUT_RAW_MAX) == round(55 * 31 / 100)  # 17
    assert percent_to_raw(50, LINEOUT_RAW_MAX) == round(50 * 31 / 100)  # 16


def test_build_get_command_uses_cget_lineout_numid():
    volume = SystemVolume(subprocess_module=FakeSubprocess())
    assert volume.build_get_command() == ["/usr/bin/amixer", "cget", "numid=3"]


def test_build_set_digital_command_uses_cset_numid_with_raw_value():
    volume = SystemVolume(subprocess_module=FakeSubprocess())
    assert volume.build_set_digital_command(55) == [
        "/usr/bin/amixer",
        "-q",
        "cset",
        "numid=2",
        "35",
    ]


def test_build_set_lineout_command_uses_cset_numid_with_raw_value():
    volume = SystemVolume(subprocess_module=FakeSubprocess())
    assert volume.build_set_lineout_command(55) == [
        "/usr/bin/amixer",
        "-q",
        "cset",
        "numid=3",
        "17",
    ]


def test_build_set_commands_clamp_percent_before_converting():
    volume = SystemVolume(subprocess_module=FakeSubprocess())
    assert volume.build_set_digital_command(500) == ["/usr/bin/amixer", "-q", "cset", "numid=2", "63"]
    assert volume.build_set_digital_command(-500) == ["/usr/bin/amixer", "-q", "cset", "numid=2", "0"]
    assert volume.build_set_lineout_command(500) == ["/usr/bin/amixer", "-q", "cset", "numid=3", "31"]
    assert volume.build_set_lineout_command(-500) == ["/usr/bin/amixer", "-q", "cset", "numid=3", "0"]


def test_get_volume_percent_parses_lineout_values_line():
    fake = FakeSubprocess(FakeCompletedProcess(stdout=CGET_OUTPUT))
    volume = SystemVolume(subprocess_module=fake)
    assert volume.get_volume_percent() == raw_to_percent(18, LINEOUT_RAW_MAX)
    assert fake.calls == [["/usr/bin/amixer", "cget", "numid=3"]]


def test_get_volume_percent_returns_none_on_nonzero_exit():
    fake = FakeSubprocess(FakeCompletedProcess(returncode=1, stderr="boom"))
    volume = SystemVolume(subprocess_module=fake)
    assert volume.get_volume_percent() is None


def test_get_volume_percent_returns_none_on_unparsable_output():
    fake = FakeSubprocess(FakeCompletedProcess(stdout="garbage"))
    volume = SystemVolume(subprocess_module=fake)
    assert volume.get_volume_percent() is None


def test_get_volume_percent_returns_none_on_missing_binary():
    fake = FakeSubprocess(exc=OSError("no such file"))
    volume = SystemVolume(subprocess_module=fake)
    assert volume.get_volume_percent() is None


def test_set_volume_percent_writes_only_the_audible_lineout_control():
    fake = FakeSubprocess(FakeCompletedProcess(returncode=0))
    volume = SystemVolume(subprocess_module=fake)
    assert volume.set_volume_percent(55) is True
    assert fake.calls == [["/usr/bin/amixer", "-q", "cset", "numid=3", "17"]]


def test_set_volume_percent_returns_false_when_lineout_fails():
    fake = FakeSubprocess(FakeCompletedProcess(returncode=1, stderr="invalid control"))
    volume = SystemVolume(subprocess_module=fake)
    assert volume.set_volume_percent(55) is False
    assert len(fake.calls) == 1


def test_set_volume_percent_logs_lineout_failure(caplog):
    fake = FakeSubprocess(FakeCompletedProcess(returncode=1, stderr="invalid control"))
    volume = SystemVolume(subprocess_module=fake)
    with caplog.at_level("WARNING"):
        volume.set_volume_percent(55)
    assert any("lineout" in record.message for record in caplog.records)


def test_set_volume_percent_returns_false_on_missing_binary():
    fake = FakeSubprocess(exc=OSError("no such file"))
    volume = SystemVolume(subprocess_module=fake)
    assert volume.set_volume_percent(55) is False


def test_never_uses_shell():
    fake = FakeSubprocess(FakeCompletedProcess(returncode=0))
    volume = SystemVolume(subprocess_module=fake)
    volume.get_volume_percent()
    volume.set_volume_percent(50)
    assert all(kwargs.get("shell") is False for kwargs in fake.kwargs)
