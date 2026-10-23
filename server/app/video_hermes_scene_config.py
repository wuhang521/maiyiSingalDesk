"""Purpose-bound Windows DPAPI snapshot for Hermes' existing Ark video key."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from . import video_hermes_config as primary

MAGIC = b"HERMES_VIDEO_SCENE_DPAPI_V1\x00"
ENTROPY = b"SignalDesk/HermesVideo/ArkScene/v1"
ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
HermesSceneConfigError = primary.HermesConfigError


def _validate(value):
    snapshot = primary._validated(value)
    if snapshot["provider"] != "ark" or snapshot["base_url"] != ARK_BASE:
        raise HermesSceneConfigError("Hermes 视频凭证必须对应北京方舟官方接口")
    if not snapshot["model"].startswith(("doubao-seedance-", "ep-")):
        raise HermesSceneConfigError("Hermes 视频模型须为已配置的 Seedance 模型或接入点")
    return snapshot


def _crypto(value, *, decrypt):
    return primary._dpapi(value, decrypt=decrypt, entropy_value=ENTROPY,
                          description="Hermes Ark scene video credential")


def save_hermes_scene_config(value, path, *, replace=False):
    snapshot = _validate(value)
    target = Path(path).expanduser().absolute()
    primary._plain_path(target)
    if target.suffix.lower() != ".dpapi":
        raise HermesSceneConfigError("视频凭证快照必须保存为 .dpapi 文件")
    if target.exists() and not replace:
        raise HermesSceneConfigError("Hermes 视频凭证已存在；需要明确指定替换")
    encrypted = MAGIC + _crypto(json.dumps(snapshot, ensure_ascii=False).encode("utf-8"), decrypt=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    primary._plain_path(target)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".hermes-scene-", suffix=".dpapi", dir=target.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        (os.replace if replace else os.rename)(temporary, target)
        temporary = None
    except OSError as error:
        raise HermesSceneConfigError("无法保存加密视频凭证，原配置未被改写") from error
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    return {"configured": True, "provider": "ark", "model": snapshot["model"],
            "protection": "Windows DPAPI CurrentUser", "path": str(target)}


def load_hermes_scene_config(path):
    target = Path(path).expanduser().absolute()
    primary._plain_path(target)
    if not target.exists():
        return {}
    if target.suffix.lower() != ".dpapi":
        raise HermesSceneConfigError("视频凭证快照路径无效")
    try:
        if not len(MAGIC) < target.stat().st_size <= primary.MAX_BYTES:
            raise HermesSceneConfigError("视频凭证快照大小无效")
        encrypted = target.read_bytes()
        if not encrypted.startswith(MAGIC):
            raise HermesSceneConfigError("该文件不是独立的视频凭证密文")
        snapshot = _validate(json.loads(_crypto(encrypted[len(MAGIC):], decrypt=True).decode("utf-8")))
    except (OSError, UnicodeError, ValueError) as error:
        raise HermesSceneConfigError("无法读取 Hermes 加密视频凭证") from error
    return {"seedance_api_key": snapshot["api_key"], "seedance_base_url": snapshot["base_url"],
            "seedance_model": snapshot["model"]}
