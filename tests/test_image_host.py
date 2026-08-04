"""The card image host.

This serves bytes on a public port with no authentication, because Tencent's
fetcher cannot log in. Everything here is therefore about two things: that the
picture is reachable when it must be, and that nothing else is.
"""
import asyncio
import io
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

if "astrbot" not in sys.modules:
    _api = types.ModuleType("astrbot.api")
    _api.logger = SimpleNamespace(**{
        name: (lambda *a, **k: None)
        for name in ("debug", "info", "warning", "error", "exception")
    })
    _root = types.ModuleType("astrbot")
    _root.api = _api
    sys.modules["astrbot"] = _root
    sys.modules["astrbot.api"] = _api

from qqofficial_hub.image_host import ImageHost  # noqa: E402


def png(colour=(200, 30, 30)) -> bytes:
    Image = pytest.importorskip("PIL.Image", reason="需要 Pillow")
    buffer = io.BytesIO()
    Image.new("RGB", (24, 24), colour).save(buffer, "PNG")
    return buffer.getvalue()


def make_host(**kwargs) -> ImageHost:
    return ImageHost(Path(tempfile.mkdtemp()), **kwargs)


# --- configuration ----------------------------------------------------------

def test_without_a_base_url_the_host_is_not_usable():
    """A local port with no public address cannot serve a card."""
    host = make_host()
    assert host.configured is False
    with pytest.raises(RuntimeError, match="公网地址"):
        host.publish(png())


def test_publishing_before_start_is_refused_clearly():
    host = make_host(base_url="https://img.example.com")
    assert host.configured is True
    with pytest.raises(RuntimeError, match="未启动"):
        host.publish(png())


# --- publishing -------------------------------------------------------------

def test_publish_returns_a_url_under_the_public_base():
    async def scenario():
        host = make_host(base_url="https://img.example.com", port=9541)
        await host.start()
        try:
            url = host.publish(png(), slot="g1")
            assert url.startswith("https://img.example.com/i/")
            assert url.endswith(".png")
        finally:
            await host.stop()
    asyncio.run(scenario())


def test_tokens_are_unguessable_rather_than_filenames():
    """The directory is reachable from the internet; predictable names would
    expose every board ever drawn."""
    async def scenario():
        host = make_host(base_url="https://x.test", port=9542)
        await host.start()
        try:
            token = host.publish(png(), slot="g1").rsplit("/", 1)[-1]
            assert len(token) >= 20
            assert "board" not in token and "g1" not in token
        finally:
            await host.stop()
    asyncio.run(scenario())


def test_only_png_and_jpeg_are_accepted():
    async def scenario():
        host = make_host(base_url="https://x.test", port=9543)
        await host.start()
        try:
            with pytest.raises(ValueError, match="PNG"):
                host.publish(b"<html>not an image</html>", slot="g1")
        finally:
            await host.stop()
    asyncio.run(scenario())


def test_oversized_images_are_refused():
    async def scenario():
        host = make_host(base_url="https://x.test", port=9544)
        await host.start()
        try:
            with pytest.raises(ValueError, match="MB"):
                host.publish(b"\x89PNG\r\n\x1a\n" + b"0" * (9 * 1024 * 1024))
        finally:
            await host.stop()
    asyncio.run(scenario())


def test_republishing_the_same_bytes_reuses_the_url():
    """A board that did not change should not spawn a second file."""
    async def scenario():
        host = make_host(base_url="https://x.test", port=9545)
        await host.start()
        try:
            data = png()
            first = host.publish(data, slot="g1")
            assert host.publish(data, slot="g1") == first
            assert host.status()["stored_images"] == 1
        finally:
            await host.stop()
    asyncio.run(scenario())


# --- one live image per group ----------------------------------------------

def test_a_new_board_retires_the_previous_one_for_that_group():
    async def scenario():
        host = make_host(base_url="https://x.test", port=9546, grace_seconds=0)
        await host.start()
        try:
            host.publish(png((1, 2, 3)), slot="g1")
            host.publish(png((9, 9, 9)), slot="g1")
            host.sweep()
            assert host.status()["stored_images"] == 1, "每群只应留最新一张"
            assert host.status()["live_slots"] == 1
        finally:
            await host.stop()
    asyncio.run(scenario())


def test_groups_do_not_retire_each_others_boards():
    async def scenario():
        host = make_host(base_url="https://x.test", port=9547, grace_seconds=0)
        await host.start()
        try:
            a = host.publish(png((1, 2, 3)), slot="group-A")
            b = host.publish(png((9, 9, 9)), slot="group-B")
            host.sweep()
            assert a != b
            assert host.status()["stored_images"] == 2
        finally:
            await host.stop()
    asyncio.run(scenario())


def test_a_superseded_image_survives_the_grace_period():
    """Tencent fetches when a viewer scrolls to the card, not when it is sent.

    Deleting the moment a replacement exists is precisely what leaves other
    people in the group looking at a broken image.
    """
    async def scenario():
        import time

        host = make_host(base_url="https://x.test", port=9548, grace_seconds=300)
        await host.start()
        try:
            old = host.publish(png((1, 2, 3)), slot="g1").rsplit("/", 1)[-1]
            host.publish(png((9, 9, 9)), slot="g1")
            host.sweep()
            assert old in host._entries, "宽限期内旧图必须还在"
            host.sweep(now=time.time() + 301)
            assert old not in host._entries, "宽限期过后应清掉"
        finally:
            await host.stop()
    asyncio.run(scenario())


def test_retiring_a_slot_lets_its_image_expire():
    async def scenario():
        host = make_host(base_url="https://x.test", port=9549, grace_seconds=0)
        await host.start()
        try:
            host.publish(png(), slot="g1")
            host.retire_slot("g1")
            host.sweep()
            assert host.status()["stored_images"] == 0
        finally:
            await host.stop()
    asyncio.run(scenario())


def test_old_images_expire_even_if_nothing_replaced_them():
    """A finished game leaves a last board that nobody supersedes."""
    async def scenario():
        import time

        host = make_host(base_url="https://x.test", port=9550,
                         max_age_seconds=60)
        await host.start()
        try:
            host.publish(png(), slot="g1")
            host.sweep(now=time.time() + 61)
            assert host.status()["stored_images"] == 0
        finally:
            await host.stop()
    asyncio.run(scenario())


# --- serving ----------------------------------------------------------------

def test_the_image_is_actually_fetchable_over_http():
    """The whole point: an anonymous GET must return the bytes."""
    async def scenario():
        aiohttp = pytest.importorskip("aiohttp")
        host = make_host(base_url="http://127.0.0.1:9551", port=9551)
        await host.start()
        try:
            data = png()
            url = host.publish(data, slot="g1")
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    assert response.status == 200
                    assert response.content_type == "image/png"
                    assert await response.read() == data
        finally:
            await host.stop()
    asyncio.run(scenario())


def test_unknown_and_traversal_paths_return_404():
    """Only tokens this process minted are servable, so a crafted path has
    nothing to reach -- it never touches the filesystem."""
    async def scenario():
        aiohttp = pytest.importorskip("aiohttp")
        host = make_host(base_url="http://127.0.0.1:9552", port=9552)
        await host.start()
        try:
            async with aiohttp.ClientSession() as session:
                for path in ("/i/nope.png", "/i/../../etc/passwd",
                             "/i/%2e%2e%2fmain.py"):
                    async with session.get(
                            f"http://127.0.0.1:9552{path}") as response:
                        assert response.status == 404, path
        finally:
            await host.stop()
    asyncio.run(scenario())


def test_a_health_endpoint_reports_liveness():
    async def scenario():
        aiohttp = pytest.importorskip("aiohttp")
        host = make_host(base_url="http://127.0.0.1:9553", port=9553)
        await host.start()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        "http://127.0.0.1:9553/healthz") as response:
                    assert response.status == 200
                    assert (await response.json())["ok"] is True
        finally:
            await host.stop()
    asyncio.run(scenario())


# --- lifecycle --------------------------------------------------------------

def test_stopping_removes_every_file():
    async def scenario():
        host = make_host(base_url="https://x.test", port=9554)
        await host.start()
        host.publish(png(), slot="g1")
        directory = host.directory
        await host.stop()
        assert list(directory.glob("*")) == []
    asyncio.run(scenario())


def test_a_restart_does_not_leave_unreachable_files_behind():
    """Tokens live in memory, so files from a previous run can never be
    served again -- keeping them would only leak disk."""
    host = make_host(base_url="https://x.test")
    stray = host.directory / "leftover.png"
    stray.write_bytes(png())
    second = ImageHost(host.directory.parent, base_url="https://x.test")
    assert not stray.exists()
    assert second.status()["stored_images"] == 0


def test_starting_twice_is_harmless():
    async def scenario():
        host = make_host(base_url="https://x.test", port=9555)
        await host.start()
        try:
            await host.start()
            assert host.running
        finally:
            await host.stop()
    asyncio.run(scenario())


# --- quick-tunnel discovery -------------------------------------------------
#
# A quick tunnel (cloudflared tunnel --url ...) gets a fresh random
# *.trycloudflare.com name on every restart, so a hard-coded base_url breaks
# after each reboot. cloudflared publishes the current one on its metrics port.

def test_discovery_reads_the_current_quick_tunnel_hostname():
    """cloudflared serves this JSON as text/plain.

    aiohttp's .json() refuses that mimetype outright, which silently made
    discovery look like "no tunnel running" -- so the body is parsed directly.
    """
    async def scenario():
        aiohttp = pytest.importorskip("aiohttp")
        from aiohttp import web

        async def quicktunnel(_request):
            # text/plain on purpose: this is what cloudflared actually sends.
            return web.Response(
                text='{"hostname":"random-words.trycloudflare.com"}',
                content_type="text/plain")

        app = web.Application()
        app.router.add_get("/quicktunnel", quicktunnel)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 20291)
        await site.start()
        try:
            host = make_host(metrics_port=20291)
            assert host.configured is False
            found = await host.discover_base_url()
            assert found == "https://random-words.trycloudflare.com"
            assert host.configured is True
        finally:
            await runner.cleanup()
    asyncio.run(scenario())


def test_discovery_is_quiet_when_no_tunnel_is_running():
    """A named tunnel with a fixed domain has nothing to discover, and that
    must not look like an error."""
    async def scenario():
        host = make_host(metrics_port=20292)
        assert await host.discover_base_url() == ""
    asyncio.run(scenario())


def test_a_configured_base_url_is_reported_in_status():
    host = make_host(base_url="https://img.example.com/")
    assert host.status()["base_url"] == "https://img.example.com"
    assert host.configured is True
