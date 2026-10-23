"""Current-user DPAPI snapshot of Hermes' existing primary text-model config.

Plaintext credentials stay in process memory. Only three Settings fields can be
returned to the video backend; this never changes video/audio provider settings.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import re
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import urlsplit


MAGIC = b"HERMES_VIDEO_PRIMARY_DPAPI_V1\x00"
ENTROPY = b"SignalDesk/HermesVideo/PrimaryModel/v1"
FIELDS = {"schema", "provider", "model", "base_url", "api_key"}
MAX_BYTES = 65536


class HermesConfigError(RuntimeError):
    """Messages are deliberately independent of secret-bearing input values."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(value: bytes):
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    return _Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi(value: bytes, *, decrypt: bool, entropy_value: bytes = ENTROPY,
           description: str = "Hermes video primary model") -> bytes:
    if os.name != "nt":
        raise HermesConfigError("Hermes 模型快照需要 Windows 当前用户 DPAPI 环境")
    source, source_buffer = _blob(value)
    entropy, entropy_buffer = _blob(entropy_value)
    output = _Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt32.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob), ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        arguments = (ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output))
    else:
        function = crypt32.CryptProtectData
        function.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob), ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        arguments = (ctypes.byref(source), description, ctypes.byref(entropy), None, None, 1, ctypes.byref(output))
    function.restype = wintypes.BOOL
    if not function(*arguments):
        raise HermesConfigError("无法保护或读取 Hermes 模型快照，请使用创建快照的同一 Windows 用户")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
        # Keep both input buffers alive through the native call.
        del source_buffer, entropy_buffer


def _validated(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != FIELDS or value.get("schema") != 1:
        raise HermesConfigError("Hermes 模型快照格式不受支持")
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in ("provider", "model", "base_url", "api_key")):
        raise HermesConfigError("Hermes 当前主模型配置不完整")
    if any(any(char in value[key] for char in "\r\n\x00") for key in ("provider", "model", "base_url", "api_key")):
        raise HermesConfigError("Hermes 模型配置包含无效控制字符")
    if len(value["api_key"]) > 8192 or len(value["model"]) > 200 or len(value["provider"]) > 100 or len(value["base_url"]) > 2048:
        raise HermesConfigError("Hermes 模型配置超出大小限制")
    for name in ("provider", "model"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@+ -]*", value[name]) or value[name].startswith(("sk-", "Bearer ")):
            raise HermesConfigError("Hermes 模型名称格式无效")
    if len(value["api_key"].strip()) < 16 or re.match(r"^(?:\$\{|\$[A-Za-z_]|os\.(?:environ|getenv)|env[:.]|getenv\()", value["api_key"].strip()):
        raise HermesConfigError("Hermes 主模型凭证尚未解析为可用配置值")
    try:
        endpoint = urlsplit(value["base_url"])
        if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise HermesConfigError("Hermes 主模型接口需要不含账户信息的 HTTPS 地址") from exc
    return {"schema": 1, "provider": value["provider"].strip(), "model": value["model"].strip(),
            "base_url": value["base_url"].strip().rstrip("/"), "api_key": value["api_key"].strip()}


def _plain_path(target: Path) -> None:
    for part in (target, *target.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise HermesConfigError("模型快照路径不能经过符号链接或目录连接")


def save_hermes_video_config(value: dict[str, Any], path: str | Path, *, replace: bool = False) -> dict[str, Any]:
    snapshot = _validated(value)
    target = Path(path).expanduser().absolute()
    _plain_path(target)
    if target.suffix.lower() != ".dpapi" or target.is_symlink():
        raise HermesConfigError("模型快照必须保存为非链接的 .dpapi 文件")
    if target.exists() and not replace:
        raise HermesConfigError("Hermes 模型快照已存在；需要明确指定替换")
    encrypted = MAGIC + _dpapi(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), decrypt=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise HermesConfigError("不能将模型快照保存到链接目录")
    temporary = None
    try:
        # The temporary file contains ciphertext only, never a plaintext JSON.
        fd, temporary = tempfile.mkstemp(prefix=".hermes-provider-", suffix=".dpapi", dir=target.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        if not replace:
            # Windows rename refuses an existing target. This also closes the
            # check/write race without truncating a user's existing snapshot.
            os.rename(temporary, target)
        else:
            os.replace(temporary, target)
        temporary = None
    except OSError as exc:
        raise HermesConfigError("无法写入加密模型快照，现有配置未被改写") from exc
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
    return {"provider": snapshot["provider"], "model": snapshot["model"], "configured": True,
            "protection": "Windows DPAPI CurrentUser", "path": str(target)}


def _read(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser().absolute()
    _plain_path(target)
    if target.suffix.lower() != ".dpapi" or target.is_symlink():
        raise HermesConfigError("Hermes 模型快照路径无效")
    try:
        size = target.stat().st_size
        if size > MAX_BYTES or size <= len(MAGIC):
            raise HermesConfigError("Hermes 模型快照大小无效")
        encrypted = target.read_bytes()
    except OSError as exc:
        raise HermesConfigError("Hermes 模型快照暂不可读") from exc
    if not encrypted.startswith(MAGIC):
        raise HermesConfigError("Hermes 模型快照不是受支持的加密文件")
    try:
        return _validated(json.loads(_dpapi(encrypted[len(MAGIC):], decrypt=True).decode("utf-8")))
    except (UnicodeError, ValueError) as exc:
        raise HermesConfigError("Hermes 模型快照内容无效") from exc


def load_hermes_video_config(path: str | Path) -> dict[str, str]:
    target = Path(path).expanduser().absolute()
    _plain_path(target)
    if not target.exists():
        return {}
    value = _read(path)
    return {"prompt_model": value["model"], "prompt_base_url": value["base_url"], "prompt_api_key": value["api_key"]}


def describe_hermes_video_config(path: str | Path) -> dict[str, Any]:
    value = _read(path)
    return {"provider": value["provider"], "model": value["model"], "configured": True, "protection": "Windows DPAPI CurrentUser"}
