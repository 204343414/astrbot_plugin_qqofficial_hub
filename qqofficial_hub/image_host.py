"""A tiny read-only image host, so cards can embed pictures.

Why this exists
---------------
QQ Markdown cards can only show an image through a **public URL**:

    对于 markdown 消息内的图片资源，请使用可在公网访问的资源 url，
    开放平台会下载转存该资源。

Rich media (``file_type=1``) accepts base64, but that produces a *separate*
image message, and QQ refuses to put rich media and a keyboard in one message.
So "a card with both a picture and buttons" has no path except a URL.

Why it cannot use AstrBot's plugin routes
-----------------------------------------
``context.register_web_api`` mounts under the dashboard, which is guarded by
``Depends(require_plugin_scope)``. Tencent's fetcher has no login token and
would get 401, so this listens on its own port with no authentication at all.
That is also why it serves nothing but bytes it wrote itself, under
unguessable names, GET only.

Lifetime: one live image per group
----------------------------------
A board is redrawn every move, so keeping every frame forever would fill the
disk with positions nobody will look at again. Each group therefore owns a
single *current* image; publishing a new one retires the previous.

Retiring is **not** an immediate delete. Tencent fetches a card's image when a
user scrolls to it -- not when it is sent -- and different viewers trigger
different fetches. Deleting the instant a replacement exists is exactly what
makes other bots show broken images, so the outgoing picture is kept for a
short grace period first.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import logger

#: Only ever served as image/jpeg or image/png; sniffed from the magic bytes.
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: A superseded image lingers this long. Covers the window where a viewer is
#: still scrolling to the card it belonged to.
DEFAULT_GRACE_SECONDS = 300
#: Backstop for images nothing ever replaced (a finished game, a restart).
DEFAULT_MAX_AGE_SECONDS = 3600
#: Refuse anything larger; a board is a few hundred KB.
MAX_IMAGE_BYTES = 8 * 1024 * 1024


@dataclass(slots=True)
class _Entry:
    token: str
    path: Path
    digest: str
    created_at: float
    #: Set when superseded; the file is deleted once this passes.
    expires_at: float | None = None
    #: How many times this image was actually fetched, and when last.
    #:
    #: This is the only honest evidence that the chain works end to end. A
    #: card can be *sent* successfully while the picture never loads (wrong
    #: public URL, tunnel down, QQ refusing the host), and nothing in the send
    #: response says so. A non-zero hit count means Tencent came and took the
    #: bytes; a zero one after a card was sent means the failure is downstream
    #: of us, not in the upload.
    hits: int = 0
    last_hit_at: float = 0.0


class ImageHost:
    """Publishes bytes at an unguessable URL and cleans up after itself."""

    def __init__(
        self,
        data_dir: Path,
        base_url: str = "",
        port: int = 9527,
        grace_seconds: int = DEFAULT_GRACE_SECONDS,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        metrics_host: str = "127.0.0.1",
        metrics_port: int = 20241,
    ) -> None:
        self.directory = Path(data_dir) / "images"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/")
        #: A base URL typed into the config is a promise the operator made
        #: (a named tunnel, a reverse proxy) and must never be overwritten by
        #: autodiscovery. An empty one means "work it out yourself, and keep
        #: working it out" -- which is the whole point of a quick tunnel whose
        #: hostname changes on every restart.
        self.pinned = bool(self.base_url)
        self.port = int(port)
        self.grace_seconds = max(int(grace_seconds), 0)
        self.max_age_seconds = max(int(max_age_seconds), 60)
        self.metrics_host = metrics_host or "127.0.0.1"
        self.metrics_port = int(metrics_port)

        self._entries: dict[str, _Entry] = {}
        #: slot key (usually a group origin) -> the token currently live there.
        self._slots: dict[str, str] = {}
        #: Aggregate fetch counters, kept across sweeps so evidence that the
        #: chain worked is not lost when the file it refers to is deleted.
        self._hits = 0
        self._misses = 0
        #: When the public address was last confirmed, and how often it moved.
        #: A rising rediscovery count is the visible symptom of a tunnel that
        #: keeps restarting, which is otherwise silent.
        self._checked_at = 0.0
        self._rediscoveries = 0
        self._last_error = ""
        self._runner: Any = None
        self._site: Any = None
        self._sweeper: asyncio.Task | None = None
        # Anything on disk from a previous run is unreachable: the tokens that
        # addressed it are gone. Clear it rather than leak it.
        self._purge_directory()

    # --- lifecycle ----------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._site is not None

    @property
    def configured(self) -> bool:
        """Whether a public base URL has been set. Without one, nothing works."""
        return bool(self.base_url.startswith("http"))

    async def discover_base_url(self) -> str:
        """Ask a local cloudflared for its current quick-tunnel hostname.

        A quick tunnel (``cloudflared tunnel --url ...``) gets a fresh random
        ``*.trycloudflare.com`` name on every restart, so hard-coding it in
        the config would break after each reboot. cloudflared exposes the
        current one on its metrics port:

            GET http://127.0.0.1:20241/quicktunnel
            {"hostname": "accent-owns-equally-expo.trycloudflare.com"}

        Returns "" when there is no quick tunnel to ask, which is the normal
        case for a named tunnel with a fixed domain.
        """
        import aiohttp

        url = f"http://{self.metrics_host}:{self.metrics_port}/quicktunnel"
        try:
            timeout = aiohttp.ClientTimeout(total=4)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return ""
                    # cloudflared serves this JSON as text/plain, so
                    # response.json() refuses it outright -- parse the body
                    # instead of trusting the declared content type.
                    payload = json.loads(await response.text())
                    hostname = str(payload.get("hostname") or "")
        except Exception:
            return ""
        if not hostname:
            return ""
        discovered = f"https://{hostname}"
        if discovered != self.base_url:
            if self.base_url:
                # Not noise: every card sent under the old name now shows a
                # broken image, and this line is the only record of why.
                self._rediscoveries += 1
                logger.warning("[QQHub] 隧道域名已变更：%s → %s（旧卡片的图会裂）",
                               self.base_url, discovered)
            else:
                logger.info("[QQHub] 自动发现隧道地址：%s", discovered)
        self.base_url = discovered
        self._checked_at = time.time()
        return discovered

    async def ensure_reachable(self) -> bool:
        """Confirm the public address still points at us, repairing it if not.

        A quick tunnel renames itself every time cloudflared restarts. Nothing
        reports this: publishing keeps succeeding, cards keep sending, and the
        only symptom is that every image silently fails to load for everyone
        in the group. Discovering the hostname once at startup therefore fixes
        the problem exactly until the first tunnel restart.

        So the address is treated as a *lease* rather than a fact, and checked
        against the local cloudflared before it is used. The check is a single
        request to a loopback metrics port -- cheap enough to run per publish,
        and far cheaper than a group full of broken images nobody reports.

        A base URL set by hand in the config is left alone: that is an
        operator's promise about their own infrastructure (a named tunnel, a
        reverse proxy), and second-guessing it would break the fixed-domain
        setup this is supposed to support.

        Returns whether an address is usable afterwards.
        """
        if self.pinned:
            return self.configured
        before = self.base_url
        discovered = await self.discover_base_url()
        if not discovered:
            # cloudflared is down or restarting. Keep the address we have: it
            # may still work, and dropping it would guarantee failure while
            # only maybe being right.
            self._last_error = "cloudflared 无响应，沿用上次的域名"
            return self.configured
        self._last_error = ""
        if before and discovered != before:
            # Every previously published URL is dead now. Republishing is the
            # caller's business; what matters here is not handing out more
            # URLs under a hostname that no longer resolves to us.
            self._invalidate_published()
        return True

    def _invalidate_published(self) -> None:
        """Forget slot ownership after the hostname moved.

        The files are still served under their tokens, so anything already
        fetched keeps working. What must not happen is a later publish into
        the same slot deciding "same bytes, reuse the old URL" -- that URL
        carries the dead hostname.
        """
        self._slots.clear()

    async def start(self) -> None:
        """Bind the local port. Idempotent."""
        if self._site is not None:
            return
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/i/{name}", self._serve)
        app.router.add_get("/healthz", self._healthz)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        # 0.0.0.0 on purpose: the tunnel may reach us from another container.
        self._site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await self._site.start()
        self._sweeper = asyncio.create_task(self._sweep_forever())
        logger.info("[QQHub] Image host listening on :%d -> %s",
                    self.port, self.base_url or "(未配置公网地址)")

    async def stop(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            self._sweeper = None
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = None
        self._site = None
        self._purge_directory()

    # --- publishing ---------------------------------------------------------

    def publish(self, data: bytes, slot: str = "") -> str:
        """Store ``data`` and return its public URL.

        ``slot`` is usually a group origin: publishing again into the same slot
        retires the previous image after the grace period, so a long game
        leaves one file behind instead of one per move.
        """
        if not self.configured:
            raise RuntimeError("图床未配置公网地址（image_host_base_url）")
        if not self.running:
            raise RuntimeError("图床未启动")
        if not data:
            raise ValueError("图片为空")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024} MB")

        suffix = self._suffix(data)
        digest = hashlib.sha256(data).hexdigest()

        # The same position rendered twice is the same file; reuse it rather
        # than writing a second copy and orphaning the first.
        existing = self._slots.get(slot)
        if existing:
            entry = self._entries.get(existing)
            if entry is not None and entry.digest == digest:
                entry.expires_at = None
                return self._url(entry)

        token = f"{secrets.token_urlsafe(18)}{suffix}"
        path = self.directory / token
        path.write_bytes(data)
        entry = _Entry(token=token, path=path, digest=digest,
                       created_at=time.time())
        self._entries[token] = entry

        if slot:
            self._retire(self._slots.get(slot))
            self._slots[slot] = token
        return self._url(entry)

    def retire_slot(self, slot: str) -> None:
        """Let a slot's image expire, e.g. when a match ends."""
        self._retire(self._slots.pop(slot, None))

    def _retire(self, token: str | None) -> None:
        entry = self._entries.get(token or "")
        if entry is None or entry.expires_at is not None:
            return
        # Not deleted now: Tencent fetches lazily, when a viewer scrolls to the
        # card. Removing it the moment a replacement exists is what leaves
        # other people looking at a broken image.
        entry.expires_at = time.time() + self.grace_seconds

    def _url(self, entry: _Entry) -> str:
        return f"{self.base_url}/i/{entry.token}"

    @staticmethod
    def _suffix(data: bytes) -> str:
        if data.startswith(_JPEG_MAGIC):
            return ".jpg"
        if data.startswith(_PNG_MAGIC):
            return ".png"
        raise ValueError("只支持 PNG 或 JPEG")

    # --- serving ------------------------------------------------------------

    async def _serve(self, request: Any) -> Any:
        from aiohttp import web

        name = request.match_info.get("name", "")
        entry = self._entries.get(name)
        # Never touch the filesystem with a caller-supplied name: only tokens
        # this process minted are servable, so traversal has nothing to reach.
        if entry is None or not entry.path.exists():
            self._misses += 1
            raise web.HTTPNotFound()
        entry.hits += 1
        entry.last_hit_at = time.time()
        self._hits += 1
        content_type = "image/png" if name.endswith(".png") else "image/jpeg"
        return web.Response(
            body=entry.path.read_bytes(),
            content_type=content_type,
            headers={"Cache-Control": "public, max-age=600"},
        )

    async def _healthz(self, _request: Any) -> Any:
        from aiohttp import web

        return web.json_response({
            "ok": True,
            "live": len(self._slots),
            "stored": len(self._entries),
            "hits": self._hits,
            "misses": self._misses,
        })

    # --- cleanup ------------------------------------------------------------

    async def _sweep_forever(self) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                self.sweep()
                # Re-check the lease even when nothing is being published.
                # Publishing already verifies, but a tunnel that restarts
                # during a quiet hour would otherwise stay broken until the
                # next card -- and the person who sends that card is the one
                # who gets the broken image. Finding out first is free.
                await self.ensure_reachable()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("[QQHub] Image sweep failed", exc_info=True)

    def sweep(self, now: float | None = None) -> int:
        """Delete expired files. Returns how many went."""
        now = time.time() if now is None else now
        removed = 0
        for token, entry in list(self._entries.items()):
            expired = entry.expires_at is not None and entry.expires_at <= now
            stale = (now - entry.created_at) > self.max_age_seconds
            if not expired and not stale:
                continue
            try:
                entry.path.unlink(missing_ok=True)
            except OSError:
                continue
            self._entries.pop(token, None)
            for slot, live in list(self._slots.items()):
                if live == token:
                    self._slots.pop(slot, None)
            removed += 1
        return removed

    def _purge_directory(self) -> None:
        for path in self.directory.glob("*"):
            try:
                path.unlink()
            except OSError:
                pass
        self._entries.clear()
        self._slots.clear()

    # --- diagnostics --------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "running": self.running,
            "base_url": self.base_url,
            "port": self.port,
            "live_slots": len(self._slots),
            "stored_images": len(self._entries),
            "grace_seconds": self.grace_seconds,
            "max_age_seconds": self.max_age_seconds,
            "metrics_endpoint": f"http://{self.metrics_host}:{self.metrics_port}",
            "hits": self._hits,
            "misses": self._misses,
            "last_hit_at": max(
                (e.last_hit_at for e in self._entries.values()), default=0.0
            ),
            "pinned": self.pinned,
            "checked_at": self._checked_at,
            "rediscoveries": self._rediscoveries,
            "last_error": self._last_error,
        }


def make_probe_png(width: int = 480, height: int = 270,
                   seed: int | None = None) -> bytes:
    """A small, obviously-fresh PNG, built without any imaging library.

    Used by the image-host self test. It has to be *visibly different* on
    every run, otherwise "the picture loaded" cannot be told apart from "QQ
    is showing me the cached one from last time" -- and that difference is
    the whole point of the test. The colour is derived from the clock, and
    the checker size from the same seed, so two consecutive probes never
    look alike.

    Pure stdlib on purpose: this must work even when Pillow is unavailable,
    because the diagnostic that only runs on healthy systems is useless.
    """
    import struct
    import zlib

    seed = int(time.time()) if seed is None else int(seed)
    r = 60 + (seed * 37) % 180
    g = 60 + (seed * 61) % 180
    b = 60 + (seed * 97) % 180
    cell = 15 + (seed % 4) * 10

    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter type 0 for this scanline
        for x in range(width):
            dark = ((x // cell) + (y // cell)) % 2 == 0
            if dark:
                rows += bytes((r, g, b))
            else:
                rows += bytes((255 - r // 2, 255 - g // 2, 255 - b // 2))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (_PNG_MAGIC
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + chunk(b"IEND", b""))
