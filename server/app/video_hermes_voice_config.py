"""Isolated current-user DPAPI credential for Hermes' existing DashScope voice.

The only environment field exported is DASHSCOPE_API_KEY. Primary model, Ark,
RunningHub and database credentials cannot enter this envelope or loader result.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from . import video_hermes_config as primary


MAGIC = b"HERMES_VIDEO_VOICE_DPAPI_V1\x00"
ENTROPY = b"SignalDesk/HermesVideo/DashScopeVoice/v1"
FIELDS = {"schema", "provider", "DASHSCOPE_API_KEY", "source"}
SOURCES = {"hermes-profile-environment", "hermes-process-environment"}
MAX_BYTES = 32768
HermesVoiceConfigError = primary.HermesConfigError


def _crypto(value: bytes, *, decrypt: bool) -> bytes:
    return primary._dpapi(value, decrypt=decrypt, entropy_value=ENTROPY, description="Hermes DashScope voice credential")


def _validated(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != FIELDS or value.get("schema") != 1 or value.get("provider") != "bailian" or value.get("source") not in SOURCES:
        raise HermesVoiceConfigError("Hermes 配音凭证快照格式不受支持")
    key = value.get("DASHSCOPE_API_KEY")
    if not isinstance(key, str) or not 16 <= len(key.strip()) <= 8192 or any(char in key for char in "\r\n\x00"):
        raise HermesVoiceConfigError("Hermes 百炼配音凭证不完整或格式无效")
    if re.match(r"^(?:\$\{|\$[A-Za-z_]|os\.(?:environ|getenv)|env[:.]|getenv\()", key.strip()):
        raise HermesVoiceConfigError("Hermes 配音凭证尚未解析，不能使用引用文本")
    return {"schema": 1, "provider": "bailian", "DASHSCOPE_API_KEY": key.strip(), "source": value["source"]}


def save_hermes_voice_config(value: dict[str, Any], path: str | Path, *, replace: bool = False) -> dict[str, Any]:
    snapshot = _validated(value)
    target = Path(path).expanduser().absolute()
    primary._plain_path(target)
    if target.suffix.lower() != ".dpapi":
        raise HermesVoiceConfigError("配音凭证快照必须保存为 .dpapi 文件")
    if target.exists() and not replace:
        raise HermesVoiceConfigError("Hermes 配音凭证快照已存在；需要明确指定替换")
    encrypted = MAGIC + _crypto(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), decrypt=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    primary._plain_path(target)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".hermes-voice-", suffix=".dpapi", dir=target.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, target)
        else:
            os.rename(temporary, target)
        temporary = None
    except OSError as exc:
        raise HermesVoiceConfigError("无法写入加密配音凭证，现有配置未被改写") from exc
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
    return {"provider": "bailian", "configured": True, "source": snapshot["source"],
            "protection": "Windows DPAPI CurrentUser", "path": str(target)}


def _read(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser().absolute()
    primary._plain_path(target)
    if target.suffix.lower() != ".dpapi":
        raise HermesVoiceConfigError("Hermes 配音凭证快照路径无效")
    try:
        size = target.stat().st_size
        if size > MAX_BYTES or size <= len(MAGIC):
            raise HermesVoiceConfigError("Hermes 配音凭证快照大小无效")
        encrypted = target.read_bytes()
    except OSError as exc:
        raise HermesVoiceConfigError("Hermes 配音凭证快照暂不可读") from exc
    if not encrypted.startswith(MAGIC):
        raise HermesVoiceConfigError("该文件不是独立的 Hermes 配音凭证密文")
    try:
        return _validated(json.loads(_crypto(encrypted[len(MAGIC):], decrypt=True).decode("utf-8")))
    except (UnicodeError, ValueError) as exc:
        raise HermesVoiceConfigError("Hermes 配音凭证快照内容无效") from exc


def load_hermes_voice_config(path: str | Path) -> dict[str, str]:
    target = Path(path).expanduser().absolute()
    primary._plain_path(target)
    if not target.exists():
        return {}
    value = _read(target)
    return {"DASHSCOPE_API_KEY": value["DASHSCOPE_API_KEY"]}


def describe_hermes_voice_config(path: str | Path) -> dict[str, Any]:
    value = _read(path)
    return {"provider": "bailian", "configured": True, "source": value["source"], "protection": "Windows DPAPI CurrentUser"}
