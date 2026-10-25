from __future__ import annotations

from puripuly_heart.ui.avatar_controls import AvatarControls


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _controls(state: dict):
    applied: list = []
    sent: list = []
    clock = _Clock()

    def apply(key, value):
        applied.append((key, value))
        state[key] = value

    controls = AvatarControls(
        get_state=lambda key: state.get(key),
        apply=apply,
        send=lambda name, value: sent.append((name, value)),
        clock=clock,
    )
    return controls, applied, sent, clock


def test_avatar_values_are_applied_as_set_not_toggle() -> None:
    state = {"trans": False}
    controls, applied, _, _ = _controls(state)

    assert controls.on_parameter("PuriPuly_Trans", True) is True
    assert controls.on_parameter("PuriPuly_Trans", True) is False  # already on: nothing
    assert applied == [("trans", True)]


def test_echo_of_what_the_app_wrote_is_ignored() -> None:
    """The loop fix: app writes True, VRChat echoes True back - no re-apply."""
    state = {"talk": True}
    controls, applied, sent, clock = _controls(state)

    controls.publish("talk", True)
    assert sent == [("PuriPuly_Talk", True)]

    state["talk"] = False  # user turned it off in the app meanwhile
    assert controls.on_parameter("PuriPuly_Talk", True) is False  # stale echo dropped
    assert applied == []

    clock.t += 5  # a real change from the menu later on is honoured
    assert controls.on_parameter("PuriPuly_Talk", True) is True


def test_sync_sends_only_changes_unless_forced() -> None:
    state = {"talk": True, "trans": False, "preset": 1}
    controls, _, sent, _ = _controls(state)

    controls.sync()
    first = dict(sent)
    assert first["PuriPuly_Talk"] is True and first["PuriPuly_Preset"] == 1
    sent.clear()

    controls.sync()
    assert sent == []

    state["trans"] = True
    controls.sync()
    assert sent == [("PuriPuly_Trans", True)]

    sent.clear()
    controls.sync(force=True)
    assert len(sent) == 3


def test_unknown_and_bad_values_are_ignored() -> None:
    controls, applied, _, _ = _controls({"preset": 0})
    assert controls.on_parameter("SomethingElse", True) is False
    assert controls.on_parameter("PuriPuly_Preset", "x") is False
    assert controls.on_parameter("PuriPuly_Preset", 2) is True
    assert applied == [("preset", 2)]


def test_receiver_routes_avatar_parameters_and_avatar_changes() -> None:
    from puripuly_heart.core.osc.receiver import VrcMicState, VrcOscReceiver

    params: list = []
    changes: list = []
    receiver = VrcOscReceiver(
        VrcMicState(),
        on_parameter=lambda name, value: params.append((name, value)),
        on_avatar_change=lambda: changes.append(1),
    )

    receiver.other_handler("/avatar/parameters/PuriPuly_Trans", True)
    receiver.other_handler("/avatar/parameters/VelocityX", 0.3)  # not ours
    receiver.other_handler("/avatar/change", "avtr_x")

    assert params == [("PuriPuly_Trans", True)]
    assert changes == [1]


def test_udp_sender_writes_avatar_parameters() -> None:
    from pythonosc.osc_message import OscMessage

    from puripuly_heart.core.osc.udp_sender import VrchatOscUdpSender

    sender = VrchatOscUdpSender()
    sent: list = []

    class _Sock:
        def sendto(self, data, addr):
            sent.append((data, addr))

        def close(self):
            pass

    sender._sock.close()
    sender._sock = _Sock()
    sender.send_parameter("PuriPuly_Trans", True)

    msg = OscMessage(sent[0][0])
    assert msg.address == "/avatar/parameters/PuriPuly_Trans"
    assert list(msg.params) == [True]
    assert sent[0][1] == ("127.0.0.1", 9000)


def test_settings_default_on_and_roundtrip() -> None:
    from puripuly_heart.config.settings import AppSettings, from_dict, to_dict

    data = to_dict(AppSettings())
    assert data["osc"]["avatar_controls"] is True
    data["osc"]["avatar_controls"] = False
    assert from_dict(data).osc.avatar_controls is False

