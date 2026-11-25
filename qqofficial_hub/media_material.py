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


async def list_images(
    client: Any,
    *,
    page_size: int = 50,
    max_pages: int = 10,
) -> list[dict[str, Any]]:
    """列出素材库里的图，**翻完所有页**。

    这个接口一页只给 20 张（不管传什么 limit 都不变），靠 ``next_cursor``
    翻页，同时用 ``total`` 报总数。只看第一页是会骗人的：素材库明明满了
    （``total=100``），第一页却只回 20 条 —— 于是一个「还剩 80 格」的结论
    会让所有写满之后的判断全部落空。
    """
    auth = _auth(client)
    images: list[dict[str, Any]] = []
    cursor = ""
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        for _ in range(max_pages):
            params = {"limit": str(int(page_size))}
            if cursor:
                params["cursor"] = cursor
            async with session.get(
                API_BASE + COLLECTION_PATH, headers=auth, params=params
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200:
                    raise MaterialError(f"素材列表失败 {resp.status}: {body}")
            batch = body.get("images") or []
            images.extend(batch)
            cursor = str(body.get("next_cursor") or "")
            total = body.get("total")
            # 两种终止条件都认：没有游标了，或者已经拿到 total 那么多
            if not cursor or not batch:
                break
            if total is not None and len(images) >= int(total):
                break
    return images


async def count_images(client: Any) -> dict[str, Any]:
    """素材库用量：``{"total": 平台报的总数, "listed": 实际翻到的条数}``。

    ``total`` 是平台自己给的、最可信；``listed`` 只用来发现「翻页没翻完」。
    """
    auth = _auth(client)
    params = {"limit": "50"}
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        async with session.get(
            API_BASE + COLLECTION_PATH, headers=auth, params=params
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise MaterialError(f"素材列表失败 {resp.status}: {body}")
    total = body.get("total")
    return {
        "total": int(total) if total is not None else None,
        "listed": len(body.get("images") or []),
        "raw": body,
    }


#: 删除素材的真实路径带 ``/delete`` 后缀：
#: ``DELETE /v2/media-images/{image_id}/delete``。
#:
#: 少了这个后缀会得到 ``{"code":11001,"err_code":40011002,"message":"不支持的调用"}``
#: —— 注意它**不是** 404，也不是权限错误，所以看起来像"这个接口不能删"。实机日志里
#: 就是这句，害得额度回收一直静默失败：素材照样上传，删不掉，100 张用完就再也发不出
#: 带图卡片。保留裸路径作为兜底，万一哪天平台两种都收。
DELETE_PATHS = ("{collection}/{image_id}/delete", "{collection}/{image_id}")


async def delete_image(client: Any, image_id: str) -> bool:
    """Free one of the 100 material slots. Already-sent messages are unaffected."""
    if not image_id:
        return False
    auth = _auth(client)
    last_error = ""
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        for template in DELETE_PATHS:
            url = API_BASE + template.format(
                collection=COLLECTION_PATH, image_id=image_id
            )
            async with session.delete(url, headers=auth) as resp:
                if resp.status < 300:
                    return True
                last_error = (await resp.text())[:200]
                # 40011002 = 这个路径不认，换下一个；其它错误没必要重试
                if "40011002" not in last_error and resp.status != 404:
                    break
    logger.warning("[QQHub] 删除素材失败 %s: %s", image_id, last_error)
    return False


def _taken_at(image: dict[str, Any]) -> float:
    """素材的创建时间（秒）。拿不到就返回 0，让它在排序里排最前。"""
    for key in ("create_time", "created_at", "upload_time", "timestamp"):
        value = image.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        # 毫秒时间戳见过，顺手归一
        return number / 1000 if number > 10_000_000_000 else number
    return 0.0


async def prune(client: Any, keep: int = 0, max_delete: int = 200) -> dict[str, Any]:
    """把素材库删到只剩最新的 ``keep`` 张，返回删了多少、失败多少。

    为什么需要「按时间删」而不是只靠 meme 自己记账：平台发 markdown 时会
    **强制转存**图片，那些副本我们没有 image_id（上传接口不回给我们），
    一条账都记不上。它们只能在这里被看见、被删掉 —— 而它们恰恰是 100 格
    里最先塞满的那一半。

    删除**不影响已经发出去的消息**：那些消息用的是转存副本，各自独立。
    """
    images = await list_images(client)
    keep = max(0, int(keep))
    if len(images) <= keep:
        return {"total": len(images), "deleted": 0, "failed": 0, "kept": len(images)}

    ordered = sorted(images, key=_taken_at)     # 最早的在前
    victims = ordered[: len(ordered) - keep][: max(0, int(max_delete))]
    deleted = failed = 0
    for image in victims:
        if await delete_image(client, str(image.get("image_id") or "")):
            deleted += 1
        else:
            failed += 1
    return {
        "total": len(images),
        "deleted": deleted,
        "failed": failed,
        "kept": len(images) - deleted,
    }
