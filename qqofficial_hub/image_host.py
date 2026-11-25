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
import builtins
import errno
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

#: Where the currently-listening host parks itself, so the next incarnation
#: can find it. AstrBot keeps the *old* plugin instance alive across a reload
#: (pending tasks still reference it), so its aiohttp site keeps holding the
#: port. Without a handover the new instance hits EADDRINUSE and the whole
#: image feature reports "拿不到公网地址" -- a wrong diagnosis of a port clash.
_LIVE_KEY = "_qqhub_image_host_live"


def _live_host() -> Any:
    return getattr(builtins, _LIVE_KEY, None)


@dataclass(slots=True)
class _Entry:
    token: str
    path: Path
    digest: str
    created_at: float
    #: Set when superseded; the file is deleted once this passes.
    expires_at: float | None = None
    #: 长期持有的图（``publish_if_absent`` 收进来的存量图库）。年龄清扫会跳过
    #: 它们 —— 它们不是「一步棋盘」，是被人长期引用的素材。
    pinned: bool = False
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
        recheck_seconds: float = 15.0,
    ) -> None:
        self.directory = Path(data_dir) / "images"
        self.directory.mkdir(parents=True, exist_ok=True)
        # Trim before anything else. A pasted URL routinely carries a leading
        # or trailing space, and an untrimmed one is the worst kind of broken:
        # `pinned` becomes True (so autodiscovery stands down) while
        # `configured` stays False (so nothing is ever served) -- a dead
        # config that reports no error and cannot heal itself.
        self.base_url = str(base_url or "").strip().rstrip("/")
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
        #: How long a confirmed address is trusted before re-probing. The
        #: background sweeper re-checks every 60s regardless, so this only
        #: collapses the bursts that happen inside one user action.
        self.recheck_seconds = float(recheck_seconds)

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
        # Deliberately *not* purging the directory here. A plugin reload
        # constructs the new instance while the old one is still alive and
        # still serving its port, so wiping the folder deleted images that
        # were live -- every card already sent went to a broken picture, and
        # the visible symptom ("图床炸了") pointed nowhere near the cause.
        # Files from a genuinely dead run are unreachable rather than
        # harmful: their tokens are gone, and the sweeper collects them by
        # age. Adoption in start() handles the reload case properly.

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
        # Debounce. A single game turn asks twice -- once to decide whether
        # the feature is available at all, once while publishing -- and each
        # probe can burn its full timeout when cloudflared is wedged. Two
        # 4-second stalls against QQ's 12-second interaction budget is enough
        # to turn a working move into "请求第三方失败", so a recent answer is
        # reused rather than re-derived.
        now = time.time()
        if (
            self.configured
            and self._checked_at
            and (now - self._checked_at) < self.recheck_seconds
        ):
            return True
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

    def _reindex_disk(self) -> int:
        """把磁盘上已有的图重新纳入索引，返回找回的个数。

        为什么需要：``_serve`` 只认**本进程生成**的 token（这是防目录穿越的
        正确做法），而 ``_entries`` 是纯内存的 —— 于是**每重启一次，之前发出
        去的所有 URL 就全部 404**，盘上的文件成了永远打不开的孤儿。

        这个代价比看上去大：已经发到群里的卡片是按 URL 取图的，重启之后
        那些卡片全部变成裂图，而且没有任何报错。

        重建是安全的：文件名本身就是 32 字符的随机 token（``secrets.token_urlsafe``），
        不可猜，所以「只服务自己写过的文件」这条性质保留 —— 只是把「自己写过的」
        从「本次进程」放宽到「本目录里的」。digest 用文件内容现算。
        """
        recovered = 0
        try:
            candidates = list(self.directory.iterdir())
        except OSError:
            return 0
        for path in candidates:
            if not path.is_file() or path.name.endswith(".part"):
                continue
            token = path.name
            # token 形状校验：长度够、只含 URL 安全字符、扩展名是图片
            if len(token) < 24 or "/" in token or "\\" in token:
                continue
            try:
                with open(path, "rb") as fh:
                    if self._suffix(fh.read(16)) == "":
                        continue
            except (OSError, ValueError):
                continue  # 不是 PNG/JPEG（或读不了），不索引
            entry = self._entries.get(token)
            if entry is not None and entry.path.exists():
                continue  # 内存里已经有更新的那条，别覆盖
            try:
                stat = path.stat()
            except OSError:
                continue
            self._entries[token] = _Entry(
                token=token,
                path=path,
                digest="",                       # 懒算：publish 的去重才需要
                created_at=stat.st_mtime,
                #: 盘上留下的图一律当长期图：分不清哪张是「上一步棋盘」、
                #: 哪张是被引用的素材，按年龄删只会删错。
                pinned=True,
            )
            recovered += 1
        return recovered

    async def start(self) -> None:
        """Bind the local port. Idempotent."""
        if self._site is not None:
            return
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/i/{name}", self._serve)
        app.router.add_get("/healthz", self._healthz)

        # 先把盘上已有的图纳入索引：否则重启之后，之前发出去的所有 URL 全部
        # 404，群里那些卡片静默变成裂图。
        recovered = self._reindex_disk()
        if recovered:
            logger.info("[QQHub] 图床重启：从磁盘找回 %s 张旧图（之前发出的 URL 继续可用）", recovered)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        # 0.0.0.0 on purpose: the tunnel may reach us from another container.
        self._site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        try:
            await self._site.start()
        except OSError as exc:
            self._site = None
            await self._runner.cleanup()
            self._runner = None
            if exc.errno != errno.EADDRINUSE:
                raise
            # Almost certainly our own predecessor: AstrBot keeps the old
            # plugin instance alive across a reload, so its listener is still
            # bound. Take the port from it rather than reporting a failure
            # that reads like a misconfiguration.
            if not await self._take_over_port():
                raise
            app = web.Application()
            app.router.add_get("/i/{name}", self._serve)
            app.router.add_get("/healthz", self._healthz)
            self._runner = web.AppRunner(app, access_log=None)
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, "0.0.0.0", self.port)
            await self._site.start()
        setattr(builtins, _LIVE_KEY, self)
        self.ensure_supervisor()
        logger.info("[QQHub] Image host listening on :%d -> %s",
                    self.port, self.base_url or "(未配置公网地址)")

    async def _take_over_port(self) -> bool:
        """Shut down a previous incarnation still holding our port.

        Returns False when the port is held by something that is not us, in
        which case the original bind error is the honest answer.
        """
        previous = _live_host()
        if previous is None or previous is self or previous.port != self.port:
            return False
        logger.warning(
            "[QQHub] 端口 %d 被上一个插件实例占用，正在接管（插件重载后的正常现象）",
            self.port,
        )
        # Inherit what it was serving: those URLs are already inside cards
        # people can still scroll to, so dropping them would break pictures
        # that are currently fine.
        try:
            await previous.stop(purge=False)
        except Exception:
            logger.warning("[QQHub] 上一个图床实例关闭失败", exc_info=True)
            return False
        self._entries.update(previous._entries)
        self._slots.update(previous._slots)
        self._hits += previous._hits
        self._misses += previous._misses
        return True

    async def stop(self, purge: bool = True) -> None:
        """Release the port. ``purge=False`` keeps the files for a successor.

        The default deletes them, which is right when the bot is shutting
        down. During a reload the incoming instance adopts them instead --
        deleting there broke every picture already sitting in a group.
        """
        if self._sweeper is not None:
            sweeper = self._sweeper
            self._sweeper = None
            sweeper.cancel()
            # Await the cancellation. cancel() only *requests* it, so without
            # this the task is still live when stop() returns -- and on a
            # reload it would keep probing on behalf of a host that no longer
            # owns the port.
            try:
                await sweeper
            except (asyncio.CancelledError, Exception):
                pass
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = None
        self._site = None
        if _live_host() is self:
            setattr(builtins, _LIVE_KEY, None)
        if purge:
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

    def publish_if_absent(self, data: bytes, slot: str = "") -> tuple[str, bool]:
        """发布一张图；同样的内容已经在图床里就直接复用，返回 ``(url, 是否新建)``。

        跟 :meth:`publish` 的区别：`publish` 按 **slot** 去重（同一个群只留一张
        「当前」图，上一张进宽限期退休），那是给「每步重画的棋盘」用的。
        存量图库要的是**按内容永久去重** —— 102 张梗图各不相同，也不该互相
        退休。所以这里按 sha256 找已存在的条目，找到就复用它、不再写一份。

        注意：不占 slot，所以这些图不会被 :meth:`_retire` 之类的回收逻辑扫走。
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

        # 内容相同就复用：先查内存索引，再查这张图自己的 digest
        for entry in self._entries.values():
            if entry.digest == digest and entry.path.exists():
                return self._url(entry), False
        # 磁盘上可能是重启前留下的（digest 没算过），按文件名找一轮
        for entry in self._entries.values():
            if entry.digest or not entry.path.exists():
                continue
            try:
                if hashlib.sha256(entry.path.read_bytes()).hexdigest() == digest:
                    entry.digest = digest
                    return self._url(entry), False
            except OSError:
                continue

        token = f"{secrets.token_urlsafe(18)}{suffix}"
        path = self.directory / token
        path.write_bytes(data)
        entry = _Entry(
            token=token, path=path, digest=digest, created_at=time.time(),
            pinned=True,  #: 存量图库：不按年龄清扫，也不设 expires_at
        )
        self._entries[token] = entry
        return self._url(entry), True

    def offline_publish_if_absent(self, data: bytes) -> tuple[str, bool]:
        """批量导入用：不需要端口、不需要图床在线，只往目录里写。

        ``publish_if_absent`` 会检查 ``configured`` / ``running``，因为线上发图
        必须拿到一个**能打开**的地址；但批量导入是另一回事 —— 它只需要把文件
        落到目录里并算出一个 URL，端点有没有起来和这件事无关。而恰恰在导入的
        时候，人往往还没把图床跑起来（甚至正是为了让图床有内容才导入）。

        去重和 ``publish_if_absent`` 完全一致：同样的内容只存一份，按 sha256
        认，包括盘上那些从上一代进程遗留下来、还没算过 digest 的文件。
        """
        if not self.configured:
            raise RuntimeError("图床未配置公网地址（image_host_base_url）")
        if not data:
            raise ValueError("图片为空")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024} MB")

        suffix = self._suffix(data)
        digest = hashlib.sha256(data).hexdigest()

        for entry in self._entries.values():
            if not entry.path.exists():
                continue
            if entry.digest == digest:
                return self._url(entry), False
            if entry.digest:
                continue
            try:
                if hashlib.sha256(entry.path.read_bytes()).hexdigest() == digest:
                    entry.digest = digest
                    return self._url(entry), False
            except OSError:
                continue

        self.directory.mkdir(parents=True, exist_ok=True)
        token = f"{secrets.token_urlsafe(18)}{suffix}"
        path = self.directory / token
        path.write_bytes(data)
        self._entries[token] = _Entry(
            token=token,
            path=path,
            digest=digest,
            created_at=time.time(),
            pinned=True,  # 存量图库：不按年龄清扫
        )
        return self._url(self._entries[token]), True

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

        # 一张图在群里被看到的每一刻，腾讯都可能再来取一次（同一个 URL、同一批
        # 字节）。它带 If-None-Match 来问「变了没」，那就答「没变」—— 304 不带
        # 响应体，省掉的是**我们自己的出口带宽**，而图床跑在隧道后面，带宽恰恰
        # 是最紧的那一环。digest 天生就是内容校验和，不必再算一次哈希。
        try:
            if not entry.digest:
                entry.digest = hashlib.sha256(entry.path.read_bytes()).hexdigest()
        except OSError:
            entry.digest = ""
        etag = f'"{entry.digest}"' if entry.digest else ""
        # getattr：不假设调用方一定带 headers（测试桩、内部调用）
        headers_in = getattr(request, "headers", None) or {}
        if etag and headers_in.get("If-None-Match", "") == etag:
            return web.Response(
                status=304,
                headers={
                    "ETag": etag,
                    "Cache-Control": "public, max-age=86400",
                },
            )

        content_type = "image/png" if name.endswith(".png") else "image/jpeg"
        headers = {"Cache-Control": "public, max-age=86400"}
        if etag:
            headers["ETag"] = etag
        return web.Response(
            body=entry.path.read_bytes(),
            content_type=content_type,
            headers=headers,
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

    def ensure_supervisor(self) -> None:
        """Start the background keeper, even when the host is not up yet.

        This used to be launched from ``start()``, which quietly created a
        dead end: no public address meant no start, no start meant no
        supervisor, and no supervisor meant nothing ever retried. Bringing
        cloudflared back up therefore fixed nothing until somebody reloaded
        the plugin by hand -- the bot could see the tunnel return and still
        insist it was missing.

        Idempotent, so callers need not track whether it is already running.
        """
        if self._sweeper is not None and not self._sweeper.done():
            return
        try:
            self._sweeper = asyncio.create_task(self._sweep_forever())
        except RuntimeError:
            # No running loop yet (constructed outside async context).
            self._sweeper = None

    async def _sweep_forever(self) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                self.sweep()
                # Force a real probe: the debounce exists to collapse bursts
                # inside one user action, and honouring it here would mean the
                # periodic check never actually checked anything.
                self._checked_at = 0.0
                # Re-check the lease even when nothing is being published.
                # Publishing already verifies, but a tunnel that restarts
                # during a quiet hour would otherwise stay broken until the
                # next card -- and the person who sends that card is the one
                # who gets the broken image. Finding out first is free.
                await self.ensure_reachable()
                # Come up on our own once an address appears. A tunnel that
                # was down at boot is the ordinary case, not an exception:
                # cloudflared and AstrBot start independently.
                if self.configured and not self.running:
                    try:
                        await self.start()
                        logger.info("[QQHub] 图床已自动恢复：%s", self.base_url)
                    except Exception:
                        logger.warning("[QQHub] 图床自动恢复失败", exc_info=True)
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
            #: 年龄清扫只针对「用完即弃」的图。长期持有的图（pinned）不按年龄
            #: 删除，但仍然尊重 expires_at —— 显式退休永远有效。
            stale = (
                not entry.pinned
                and entry.expires_at is None
                and (now - entry.created_at) > self.max_age_seconds
            )
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
