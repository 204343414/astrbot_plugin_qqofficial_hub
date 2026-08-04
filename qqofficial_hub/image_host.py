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


class ImageHost:
    """Publishes bytes at an unguessable URL and cleans up after itself."""

    def __init__(
        self,
        data_dir: Path,
        base_url: str = "",
        port: int = 9527,
        grace_seconds: int = DEFAULT_GRACE_SECONDS,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    ) -> None:
        self.directory = Path(data_dir) / "images"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/")
        self.port = int(port)
        self.grace_seconds = max(int(grace_seconds), 0)
        self.max_age_seconds = max(int(max_age_seconds), 60)

        self._entries: dict[str, _Entry] = {}
        #: slot key (usually a group origin) -> the token currently live there.
        self._slots: dict[str, str] = {}
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
            raise web.HTTPNotFound()
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
        })

    # --- cleanup ------------------------------------------------------------

    async def _sweep_forever(self) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                self.sweep()
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
        }
