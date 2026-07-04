"""Contract tests for the Twitch chat surface (protocol parsing + message selection)."""

import random

from twitch_bot import (
    ChatMessage,
    SelectionState,
    chat_msgs_per_min,
    mark_replied,
    parse_irc_line,
    should_respond,
)


class _NeverRng(random.Random):
    def random(self):
        return 1.0


class _AlwaysRng(random.Random):
    def random(self):
        return 0.0


def test_parse_privmsg_with_tags() -> None:
    line = ("@badge-info=;color=#FF0000 :someviewer!someviewer@someviewer.tmi.twitch.tv "
            "PRIVMSG #korokich :hi koroki!!")
    msg = parse_irc_line(line)
    assert isinstance(msg, ChatMessage)
    assert msg.login == "someviewer"
    assert msg.channel == "korokich"
    assert msg.text == "hi koroki!!"


def test_parse_ping_and_noise() -> None:
    assert parse_irc_line("PING :tmi.twitch.tv") == "PING"
    assert parse_irc_line(":tmi.twitch.tv 001 nick :Welcome") is None
    assert parse_irc_line("") is None


def test_name_mention_always_selected() -> None:
    s = SelectionState()
    msg = ChatMessage("viewer", "KOROKI what's your favorite song?", "ch")
    s.recent_msg_ts.append(1000.0)
    assert should_respond(s, msg, 1000.0, rng=_NeverRng())


def test_global_cooldown_blocks() -> None:
    s = SelectionState()
    msg = ChatMessage("viewer", "koroki hi", "ch")
    mark_replied(s, ChatMessage("other", "x", "ch"), 995.0)
    assert not should_respond(s, msg, 1000.0, rng=_AlwaysRng())


def test_per_user_cooldown_blocks() -> None:
    s = SelectionState()
    msg = ChatMessage("viewer", "koroki hi again", "ch")
    s.per_user_last_ts["viewer"] = 990.0
    assert not should_respond(s, msg, 1000.0, rng=_AlwaysRng())


def test_ambient_sampling_scales_with_chat_speed() -> None:
    slow, busy = SelectionState(), SelectionState()
    now = 1000.0
    slow.recent_msg_ts = [now - i for i in range(3)]          # ~3 msg/min
    busy.recent_msg_ts = [now - i * 0.2 for i in range(200)]  # very busy
    assert chat_msgs_per_min(slow, now) < chat_msgs_per_min(busy, now)

    class _P(random.Random):
        def __init__(self, v):
            super().__init__()
            self.v = v

        def random(self):
            return self.v

    msg = ChatMessage("viewer", "just chatting along", "ch")
    # p ≈ 0.1 in slow chat vs the 0.02 floor in busy chat — 0.05 separates them
    assert should_respond(slow, msg, now, rng=_P(0.05))
    assert not should_respond(busy, msg, now, rng=_P(0.05))


def test_mark_replied_updates_state() -> None:
    s = SelectionState()
    mark_replied(s, ChatMessage("viewer", "x", "ch"), 1234.0)
    assert s.last_reply_ts == 1234.0
    assert s.per_user_last_ts["viewer"] == 1234.0
