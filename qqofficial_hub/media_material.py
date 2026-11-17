"""QQ 开放平台「素材管理」：让卡片里的图片不再依赖任何公网 URL。

为什么需要它：卡片 Markdown 里的图片不是随消息上传的，而是**腾讯服务端自己去抓
那个 URL 再转存**。抓不到、或者转存失败，发消息接口默认**不报错**，群里看到的
只是一张裂图 —— 图床、隧道、语法全对也没用，因为失败发生在平台内部，不在我们
这一侧的任何一环。排查时很容易把时间全花在图床上。

素材管理接口（``/v2/media-images``）换了个思路：把字节直接存进腾讯自己的对象
存储，拿到 ``qqbot-media://image/v1/<token>`` 引用，再把这个引用写进 markdown。
平台不需要访问我们的任何地址，"抓不到"这件事就不存在了。

三步流程（v2 文档）：
  1. ``POST /v2/media-images/prepare``  申报文件名/大小/类型/md5/sha1
     → ``upload_id`` + 预签名 URL
  2. ``PUT  <预签名 URL>``              直传字节
  3. ``POST /v2/media-images/complete`` 提交 ``upload_id`` + ``md5``
     → ``image_id`` + ``url``（qqbot-media 引用）+ ``ttl``

实测踩到的坑：**PUT 的 Content-Type 必须与 prepare 申报的一致**。预签名把
Content-Type 签进了 URL，头不对 COS 直接 403 AccessDenied，而错误信息里只有
"Access Denied."，看不出是头的问题。application/octet-stream 同样被拒 —— 猜
"二进制就该用 octet-stream" 会在这里卡很久。

平台限制：仅 png/jpeg/gif/webp、单张 ≤10MB、每个机器人最多 100 张、有效期 90 天。
100 张对"每走一步重画一张棋盘"的游戏是真约束，所以 :func:`delete_image` 必须
配合使用 —— 新卡片取代旧卡片时把旧素材删掉，否则几盘棋就用光额度。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

import aiohttp

logger = logging.getLogger("qqofficial_hub.media_material")

API_BASE = "https://api.sgroup.qq.com"
PREPARE_PATH = "/v2/media-images/prepare"
COMPLETE_PATH = "/v2/media-images/complete"
COLLECTION_PATH = "/v2/media-images"
MAX_BYTES = 10 * 1024 * 1024
TIMEOUT = aiohttp.ClientTimeout(total=20)


class MaterialError(RuntimeError):
    """素材接口拒绝或返回了不合规的内容。"""


def _auth(client: Any) -> dict[str, str]:
    """借 botpy 现成的鉴权头，避免自己去碰 AppSecret。

    botpy 的 ``BotHttp`` 每 60 秒刷新一次 access_token 并写在 ``_headers`` 里，
    复用它既不用碰密钥，也不会把 token 缓存成一份过期的副本。
    """
    http = getattr(getattr(client, "api", None), "_http", None)
    headers = getattr(http, "_headers", None) or {}
    for key in ("Authorization", "authorization"):
        value = headers.get(key)
        if value:
            return {"Authorization": value}
    raise MaterialError("拿不到 QQ 接口鉴权头（botpy client 未就绪）")


def sniff(data: bytes) -> tuple[str, str]:
    """Return ``(content_type, suffix)`` for the formats the API accepts.

    Decided from magic bytes rather than the caller's word: an image rendered
    in-process has no filename, and declaring the wrong type is rejected at the
    PUT step with a message that says nothing about types.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"GIF8"):
        return "image/gif", ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise MaterialError("素材接口只收 png/jpeg/gif/webp")


async def upload_image(client: Any, data: bytes, file_name: str = "") -> dict[str, Any]:
    """Upload one image, returning ``{"image_id", "url", "ttl"}``.

    ``url`` is a ``qqbot-media://…`` reference: it belongs inside Markdown image
    syntax and nothing else. It is not a link, and it is not permanent -- the
    platform reports a TTL (90 days) and expires it.
    """
    if not data:
        raise MaterialError("图片为空")
    if len(data) > MAX_BYTES:
        raise MaterialError(f"素材单张上限 {MAX_BYTES // 1024 // 1024}MB")
    content_type, suffix = sniff(data)
    md5 = hashlib.md5(data).hexdigest()
    sha1 = hashlib.sha1(data).hexdigest()
    auth = _auth(client)
    payload = {
        "file_name": file_name or f"{md5[:16]}{suffix}",
        "file_size": len(data),
        "content_type": content_type,
        "md5": md5,
        "sha1": sha1,
    }
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        async with session.post(API_BASE + PREPARE_PATH, json=payload,
                                headers=auth) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise MaterialError(f"prepare 失败 {resp.status}: {body}")
        upload_id = str(body.get("upload_id") or "")
        presigned = str(body.get("presigned_url") or "")
        if not upload_id or not presigned:
            raise MaterialError(f"prepare 返回缺字段: {sorted(body)}")
        async with session.put(presigned, data=data,
                               headers={"Content-Type": content_type}) as resp:
            etag = (resp.headers.get("ETag") or "").strip('"')
            if resp.status >= 300:
                raise MaterialError(
                    f"直传失败 {resp.status}: {(await resp.text())[:200]}")
        complete = {"upload_id": upload_id, "md5": md5}
        if etag:
            complete["etag"] = etag
        async with session.post(API_BASE + COMPLETE_PATH, json=complete,
                                headers=auth) as resp:
            result = await resp.json(content_type=None)
            if resp.status != 200:
                raise MaterialError(f"complete 失败 {resp.status}: {result}")
    if not result.get("url"):
        raise MaterialError(f"complete 返回缺 url: {sorted(result)}")
    logger.info("[QQHub] 素材已上传 image_id=%s ttl=%s",
                result.get("image_id"), result.get("ttl"))
    return result


async def list_images(client: Any) -> list[dict[str, Any]]:
    auth = _auth(client)
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        async with session.get(API_BASE + COLLECTION_PATH, headers=auth) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise MaterialError(f"素材列表失败 {resp.status}: {body}")
    return body.get("images") or []


async def delete_image(client: Any, image_id: str) -> bool:
    """Free one of the 100 material slots. Already-sent messages are unaffected."""
    if not image_id:
        return False
    auth = _auth(client)
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        async with session.delete(f"{API_BASE}{COLLECTION_PATH}/{image_id}",
                                  headers=auth) as resp:
            if resp.status >= 300:
                logger.warning("[QQHub] 删除素材失败 %s: %s", image_id,
                               (await resp.text())[:200])
                return False
    return True
