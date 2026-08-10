# -*- coding: utf-8 -*-
"""Tests for the Steam bridge's new-message + echo-suppression logic."""
from tracker import MessageTracker, Msg


def m(speaker, text):
    return Msg(speaker, text)


def test_first_poll_emits_nothing():
    """The scrollback already on screen is history, not new arrivals."""
    t = MessageTracker(own_name="Me")
    out = t.poll([m("Aba", "你好"), m("Me", "hi")])
    assert out == []


def test_a_new_inbound_message_is_emitted():
    t = MessageTracker(own_name="Me")
    t.poll([m("Aba", "你好")])
    out = t.poll([m("Aba", "你好"), m("Aba", "在吗")])
    assert out == [m("Aba", "在吗")]


def test_our_own_messages_are_never_inbound():
    t = MessageTracker(own_name="Me")
    t.poll([m("Aba", "你好")])
    out = t.poll([m("Aba", "你好"), m("Me", "hello there")])
    assert out == []


def test_echo_suppressed_even_without_own_name():
    """If we could not read our display name, a message matching what we just
    sent is still not shown back to us."""
    t = MessageTracker(own_name="")
    t.poll([m("Aba", "你好")])
    t.note_sent("你好吗")
    out = t.poll([m("Aba", "你好"), m("someone", "你好吗")])
    assert out == []


def test_echo_is_consumed_once_so_a_real_later_repeat_shows():
    t = MessageTracker(own_name="")
    t.poll([m("Aba", "start")])
    t.note_sent("ok")
    # our echo appears and is swallowed
    assert t.poll([m("Aba", "start"), m("X", "ok")]) == []
    # later the friend genuinely says the same word — it must show
    out = t.poll([m("Aba", "start"), m("X", "ok"), m("Aba", "ok")])
    assert out == [m("Aba", "ok")]


def test_ordinary_repeats_from_friend_are_kept():
    t = MessageTracker(own_name="Me")
    t.poll([m("Aba", "ok")])
    out = t.poll([m("Aba", "ok"), m("Aba", "ok")])
    assert out == [m("Aba", "ok")]


def test_multiple_new_messages_in_one_poll():
    t = MessageTracker(own_name="Me")
    t.poll([m("Aba", "1")])
    out = t.poll([m("Aba", "1"), m("Aba", "2"), m("Aba", "3")])
    assert out == [m("Aba", "2"), m("Aba", "3")]


def test_scrolled_window_still_finds_new_tail():
    """Steam keeps only the last N blocks; older ones fall off the top."""
    t = MessageTracker(own_name="Me")
    t.poll([m("Aba", "a"), m("Aba", "b"), m("Aba", "c")])
    # 'a' scrolled off, 'd' is new
    out = t.poll([m("Aba", "b"), m("Aba", "c"), m("Aba", "d")])
    assert out == [m("Aba", "d")]


def test_blank_messages_ignored():
    t = MessageTracker(own_name="Me")
    t.poll([m("Aba", "x")])
    out = t.poll([m("Aba", "x"), m("Aba", "   ")])
    assert out == []


def test_no_change_emits_nothing():
    t = MessageTracker(own_name="Me")
    snap = [m("Aba", "hi"), m("Me", "yo")]
    t.poll(snap)
    assert t.poll(list(snap)) == []
