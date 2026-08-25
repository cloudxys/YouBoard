#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""YouBoard 云同步模块（最小闭环：GitHub Gist / WebDAV）

设计：
- 端到端加密：本地历史先解密，再用用户设置的「同步密码」加密成同步包
- 云端只保存密文；换设备输入同一密码即可恢复
- 手动同步：上传覆盖远端；下载按 hash / id 去重合并，保留时间较新的条目
- Token / 密码在 Windows 上用 DPAPI 加密后存入配置，不落明文
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    HAS_CRYPTO = True
except Exception:
    HAS_CRYPTO = False

SYNC_FILE_NAME = "youboard_sync.bin"
SYNC_VERSION = 1
_PBKDF2_ITER = 200_000
IS_WIN = (sys.platform == "win32")


class SyncError(Exception):
    """云同步业务错误（消息可直接展示给用户）。"""


# ===========================================================================
# 同步包加密（同步密码 -> PBKDF2 -> Fernet）
# ===========================================================================

def _derive_key(passphrase, salt):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=salt, iterations=_PBKDF2_ITER)
    return base64.urlsafe_b64encode(
        kdf.derive(passphrase.encode("utf-8")))


def encrypt_bundle(payload, passphrase):
    """把可 JSON 序列化的 payload 加密成同步包字节。"""
    if not HAS_CRYPTO:
        raise SyncError("缺少 cryptography 组件，无法加密同步包")
    if not passphrase or len(passphrase) < 4:
        raise SyncError("同步密码至少需要 4 位")
    salt = os.urandom(16)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    token = Fernet(_derive_key(passphrase, salt)).encrypt(raw)
    return salt + token


def decrypt_bundle(blob, passphrase):
    """解密同步包并返回 payload 字典。"""
    if not HAS_CRYPTO:
        raise SyncError("缺少 cryptography 组件，无法解密同步包")
    if not passphrase:
        raise SyncError("请输入同步密码")
    try:
        salt, token = blob[:16], blob[16:]
        raw = Fernet(_derive_key(passphrase, salt)).decrypt(token)
        data = json.loads(raw.decode("utf-8"))
    except InvalidToken:
        raise SyncError("同步密码错误，或文件已损坏")
    except Exception:
        raise SyncError("同步文件解析失败")
    if not isinstance(data, dict):
        raise SyncError("同步文件格式不正确")
    return data


# ===========================================================================
# 密钥存储：Windows DPAPI，非 Windows 退化为 base64（非明文）
# ===========================================================================

def protect_secret(text):
    """把敏感字符串加密后返回可存配置的字符串。"""
    if not text:
        return ""
    if IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes

            class DATA_BLOB(ctypes.Structure):
                _fields_ = [("cbData", wintypes.DWORD),
                            ("pbData", ctypes.POINTER(ctypes.c_char))]

            raw = text.encode("utf-8")
            buf = ctypes.create_string_buffer(raw, len(raw))
            in_blob = DATA_BLOB(len(raw), ctypes.cast(
                buf, ctypes.POINTER(ctypes.c_char)))
            out_blob = DATA_BLOB()
            crypt32 = ctypes.windll.crypt32
            crypt32.CryptProtectData.argtypes = [
                ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR,
                ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                ctypes.c_void_p, wintypes.DWORD,
                ctypes.POINTER(DATA_BLOB)]
            crypt32.CryptProtectData.restype = wintypes.BOOL
            if crypt32.CryptProtectData(
                    ctypes.byref(in_blob), None, None, None, None, 0,
                    ctypes.byref(out_blob)) and out_blob.pbData:
                data = ctypes.string_at(out_blob.pbData, out_blob.cbData)
                ctypes.windll.kernel32.LocalFree(out_blob.pbData)
                return "dpapi:" + base64.b64encode(data).decode("ascii")
        except Exception:
            pass
    return "b64:" + base64.b64encode(text.encode("utf-8")).decode("ascii")


def unprotect_secret(stored):
    """还原 protect_secret 的存储值。"""
    if not stored:
        return ""
    try:
        if stored.startswith("dpapi:"):
            import ctypes
            from ctypes import wintypes

            class DATA_BLOB(ctypes.Structure):
                _fields_ = [("cbData", wintypes.DWORD),
                            ("pbData", ctypes.POINTER(ctypes.c_char))]

            raw = base64.b64decode(stored[6:])
            buf = ctypes.create_string_buffer(raw, len(raw))
            in_blob = DATA_BLOB(len(raw), ctypes.cast(
                buf, ctypes.POINTER(ctypes.c_char)))
            out_blob = DATA_BLOB()
            crypt32 = ctypes.windll.crypt32
            crypt32.CryptUnprotectData.argtypes = [
                ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
                ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                ctypes.c_void_p, wintypes.DWORD,
                ctypes.POINTER(DATA_BLOB)]
            crypt32.CryptUnprotectData.restype = wintypes.BOOL
            if crypt32.CryptUnprotectData(
                    ctypes.byref(in_blob), None, None, None, None, 0,
                    ctypes.byref(out_blob)) and out_blob.pbData:
                data = ctypes.string_at(out_blob.pbData, out_blob.cbData)
                ctypes.windll.kernel32.LocalFree(out_blob.pbData)
                return data.decode("utf-8")
            return ""
        if stored.startswith("b64:"):
            return base64.b64decode(stored[4:]).decode("utf-8")
    except Exception:
        return ""
    return stored  # 兼容旧明文


# ===========================================================================
# 同步客户端
# ===========================================================================

class SyncClient:
    """上传 / 下载同步包字节的客户端基类。"""

    def upload(self, data):
        raise NotImplementedError

    def download(self):
        raise NotImplementedError


class GistSyncClient(SyncClient):
    """GitHub Gist：文件内容为 base64 文本（同步包本身是二进制密文）。"""

    def __init__(self, token, gist_id=None, api_base="https://api.github.com"):
        self.token = (token or "").strip()
        self.gist_id = (gist_id or "").strip()
        self.api_base = (api_base or "https://api.github.com").rstrip("/")

    def _request(self, url, method, body=None):
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "YouBoard")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "ignore")
            raise SyncError("GitHub 请求失败 (HTTP %s)：%s" % (e.code, detail))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SyncError("无法连接 GitHub：%s" % e)

    def upload(self, data):
        content = base64.b64encode(data).decode("ascii")
        body = json.dumps({
            "description": "YouBoard 剪贴板历史同步",
            "public": False,
            "files": {SYNC_FILE_NAME: {"content": content}},
        }).encode("utf-8")
        if self.gist_id:
            _, resp = self._request(
                self.api_base + "/gists/" + self.gist_id, "PATCH", body)
            return self.gist_id
        _, resp = self._request(self.api_base + "/gists", "POST", body)
        try:
            return json.loads(resp.decode("utf-8")).get("id") or ""
        except Exception:
            return ""

    def download(self):
        if not self.gist_id:
            raise SyncError("还没有关联的 Gist，请先「上传到云端」一次")
        _, resp = self._request(
            self.api_base + "/gists/" + self.gist_id, "GET")
        try:
            files = (json.loads(resp.decode("utf-8")) or {}).get("files") or {}
            content = (files.get(SYNC_FILE_NAME) or {}).get("content") or ""
        except Exception:
            raise SyncError("Gist 返回内容解析失败")
        if not content:
            raise SyncError("Gist 中未找到同步文件")
        try:
            return base64.b64decode(content)
        except Exception:
            raise SyncError("Gist 同步文件解码失败")


class WebDAVSyncClient(SyncClient):
    """WebDAV：把同步包直接 PUT / GET 到目录地址下的固定文件名。"""

    def __init__(self, url, username="", password=""):
        base = (url or "").strip().rstrip("/")
        if not base:
            raise SyncError("WebDAV 地址不能为空")
        self.base = base
        self.username = (username or "").strip()
        self.password = password or ""
        self.file_url = base + "/" + SYNC_FILE_NAME

    def _headers(self):
        headers = {"User-Agent": "YouBoard"}
        if self.username or self.password:
            token = base64.b64encode(
                ("%s:%s" % (self.username, self.password)).encode("utf-8")
            ).decode("ascii")
            headers["Authorization"] = "Basic " + token
        return headers

    def _open(self, method, data=None):
        req = urllib.request.Request(self.file_url, data=data, method=method)
        for k, v in self._headers().items():
            req.add_header(k, v)
        try:
            return urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as e:
            if method == "GET" and e.code == 404:
                raise SyncError("云端还没有同步文件，请先「上传到云端」")
            raise SyncError("WebDAV %s 失败 (HTTP %s)" % (
                "上传" if method == "PUT" else "下载", e.code))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SyncError("无法连接 WebDAV 服务器：%s" % e)

    def upload(self, data):
        with self._open("PUT", data):
            return True

    def download(self):
        with self._open("GET") as resp:
            return resp.read()
