"""素材管理上传：三步流程、Content-Type 陷阱、鉴权头来源。

网络在这一层被换成一个记录用的假 session —— 要断言的是**我们发出去的请求
长什么样**（尤其 PUT 的 Content-Type），而不是腾讯服务器今天的状态。
"""
import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

from qqofficial_hub import media_material  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


class _Resp:
    def __init__(self, status=200, body=None, headers=None, text=""):
        self.status = status
        self._body = body or {}
        self.headers = headers or {}
        self._text = text

    async def json(self, content_type=None):
        return self._body

    async def text(self):
        return self._text


class _Ctx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Records every request; answers by URL suffix."""

    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        _Session.calls.append(("POST", url, headers, json))
        if url.endswith("/prepare"):
            return _Ctx(_Resp(body={
                "upload_id": "up_1",
                "presigned_url": "https://cos.example.com/part_1?sig=1",
                "expires_in": 3600,
            }))
        return _Ctx(_Resp(body={
            "image_id": "102824564_abc",
            "url": "qqbot-media://image/v1/tok",
            "ttl": 7776000,
        }))

    def put(self, url, data=None, headers=None):
        _Session.calls.append(("PUT", url, headers, len(data or b"")))
        return _Ctx(_Resp(status=200, headers={"ETag": '"deadbeef"'}))

    def get(self, url, headers=None):
        _Session.calls.append(("GET", url, headers, None))
        return _Ctx(_Resp(body={"images": [{"image_id": "x"}]}))

    def delete(self, url, headers=None):
        _Session.calls.append(("DELETE", url, headers, None))
        return _Ctx(_Resp(status=204))


class _Client:
    """Just enough botpy: api._http._headers carrying the auth header."""

    def __init__(self, auth="QQBot TOKEN123"):
        http = SimpleNamespace(_headers={} if auth is None else {"Authorization": auth})
        self.api = SimpleNamespace(_http=http)


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    _Session.calls = []
    monkeypatch.setattr(media_material.aiohttp, "ClientSession", _Session)


def _upload(data=PNG, client=None, **kw):
    return asyncio.run(media_material.upload_image(client or _Client(), data, **kw))


def test_upload_returns_the_qqbot_media_reference():
    result = _upload()
    assert result["url"] == "qqbot-media://image/v1/tok"
    assert result["ttl"] == 7776000


def test_put_uses_the_declared_content_type():
    """The presigned URL signs Content-Type; a mismatch is a bare 403.

    Tried for real: omitting the header, or sending
    ``application/octet-stream``, both come back "Access Denied." with no hint
    that the header was the problem. This test exists so nobody "cleans up"
    that header later.
    """
    _upload()
    put = [c for c in _Session.calls if c[0] == "PUT"][0]
    assert put[2] == {"Content-Type": "image/png"}
    assert put[3] == len(PNG)


def test_jpeg_is_declared_as_jpeg():
    _upload(data=JPEG)
    put = [c for c in _Session.calls if c[0] == "PUT"][0]
    assert put[2]["Content-Type"] == "image/jpeg"


def test_prepare_declares_size_and_hashes():
    _upload()
    prepare = [c for c in _Session.calls if str(c[1]).endswith("/prepare")][0]
    body = prepare[3]
    assert body["file_size"] == len(PNG)
    assert body["content_type"] == "image/png"
    assert len(body["md5"]) == 32 and len(body["sha1"]) == 40


def test_auth_header_is_borrowed_from_botpy():
    _upload()
    prepare = [c for c in _Session.calls if str(c[1]).endswith("/prepare")][0]
    assert prepare[2]["Authorization"] == "QQBot TOKEN123"


def test_missing_auth_header_fails_before_uploading():
    with pytest.raises(media_material.MaterialError):
        _upload(client=_Client(auth=None))
    assert not [c for c in _Session.calls if c[0] == "POST"]


def test_unknown_format_is_rejected_locally():
    with pytest.raises(media_material.MaterialError):
        _upload(data=b"BM" + b"\x00" * 20)


def test_empty_and_oversized_images_are_rejected():
    with pytest.raises(media_material.MaterialError):
        _upload(data=b"")
    with pytest.raises(media_material.MaterialError):
        _upload(data=PNG + b"\x00" * media_material.MAX_BYTES)
