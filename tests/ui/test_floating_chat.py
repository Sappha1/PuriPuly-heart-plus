from __future__ import annotations

import asyncio
import json

import pytest

from puripuly_heart.ui.floating_chat_server import FloatingChatHub
from puripuly_heart.ui.floating_chat_window import parse_args


async def _client(hub: FloatingChatHub, token: str):
    reader, writer = await asyncio.open_connection("127.0.0.1", hub.port)
    writer.write((json.dumps({"type": "hello", "token": token}) + "\n").encode())
    await writer.drain()
    return reader, writer


async def _read(reader) -> dict:
    return json.loads(await asyncio.wait_for(reader.readline(), 2.0))


@pytest.mark.asyncio
async def test_client_gets_history_then_new_lines() -> None:
    hub = FloatingChatHub(on_submit=lambda text: None)
    await hub.start()
    try:
        hub.publish(channel="self", source_text="hello", translated_text="안녕", time="12:00")
        reader, writer = await _client(hub, hub.token)

        history = await _read(reader)
        assert history["type"] == "history"
        assert [e["translation"] for e in history["entries"]] == ["안녕"]

        await asyncio.sleep(0.05)  # handler registered the client
        hub.publish(channel="peer", source_text="こんにちは", translated_text="hi", speaker="Alex")
        entry = await _read(reader)
        assert (entry["type"], entry["channel"], entry["speaker"]) == ("entry", "peer", "Alex")
        writer.close()
    finally:
        await hub.stop()


@pytest.mark.asyncio
async def test_typed_text_is_submitted_like_the_main_chat_box() -> None:
    submitted: list[str] = []
    hub = FloatingChatHub(on_submit=submitted.append)
    await hub.start()
    try:
        reader, writer = await _client(hub, hub.token)
        await _read(reader)
        writer.write((json.dumps({"type": "submit", "text": "  see you  "}) + "\n").encode())
        await writer.drain()
        for _ in range(50):
            if submitted:
                break
            await asyncio.sleep(0.02)
        assert submitted == ["see you"]
        writer.close()
    finally:
        await hub.stop()


@pytest.mark.asyncio
async def test_a_wrong_token_is_dropped_without_data() -> None:
    hub = FloatingChatHub(on_submit=lambda text: None)
    await hub.start()
    try:
        hub.publish(channel="self", source_text="secret line", translated_text="x")
        reader, writer = await _client(hub, "not-the-token")
        assert await asyncio.wait_for(reader.readline(), 2.0) == b""
        assert hub.client_count == 0
        writer.close()
    finally:
        await hub.stop()


def test_only_chat_lines_are_mirrored() -> None:
    hub = FloatingChatHub(on_submit=lambda text: None)
    hub.publish(channel="ocr", source_text="captured", translated_text="x")
    hub.publish(channel="self", source_text="a", translated_text="b")
    assert [e["channel"] for e in hub._history] == ["self"]


def test_window_arguments() -> None:
    argv = ["--floating-chat", "--port", "5123", "--token", "abc", "--locale", "ja"]
    assert parse_args(argv) == (5123, "abc", "ja")
    assert parse_args(["--floating-chat"]) == (0, "", "en")


def test_dashboard_mirrors_new_lines_but_not_reflows_or_ocr() -> None:
    import flet as ft

    from puripuly_heart.ui.views.dashboard import DashboardView

    dash = DashboardView.__new__(DashboardView)
    dash._chat_list_view = None  # stop right after the mirror call
    seen: list[dict] = []
    dash.on_chat_entry_mirror = lambda **kw: seen.append(kw)

    # with no chat list the method returns before mirroring; give it one
    dash._chat_list_view = ft.Column()
    try:
        dash.append_chat_entry(channel="self", source="Mic", source_text="hi",
                               translated_text="안녕")
    except Exception:
        pass  # the rest of the render needs a full dashboard
    try:
        dash.append_chat_entry(channel="ocr", source="ocr", source_text="x", translated_text="y")
    except Exception:
        pass
    try:
        dash.append_chat_entry(channel="peer", source="Peer", source_text="a",
                               translated_text="b", insert_at=0)
    except Exception:
        pass

    assert [(e["channel"], e["translated_text"]) for e in seen] == [("self", "안녕")]
