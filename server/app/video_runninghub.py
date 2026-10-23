"""Fixed RunningHub Mini scene routes, exact-payload estimates, and safe receipts."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_UP
from collections import OrderedDict
from copy import deepcopy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from fastapi import HTTPException
import httpx


SCENE_MODEL = "sparkvideo-2.0-mini"
BASE_URL = "https://www.runninghub.cn"
T2V_ROUTE = "/openapi/v2/rhart-video/sparkvideo-2.0-mini/text-to-video"
I2V_ROUTE = "/openapi/v2/rhart-video/sparkvideo-2.0-mini/image-to-video"
SOURCE_URL = "https://www.runninghub.cn/runninghub-api-doc-cn/api-454850620"
VERIFIED_AT = "2026-10-22"
TIMEOUT_SECONDS = 12
_TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_QUOTE_CACHE: OrderedDict[tuple[str, str], dict] = OrderedDict()
_QUOTE_CACHE_LOCK = threading.Lock()
_QUOTE_CACHE_MAX = 256


def scene_constraints() -> dict[str, Any]:
    return {"duration_min": 4, "duration_max": 15, "duration_step": 1,
            "resolutions": ["480p", "720p"], "ratios": ["9:16"],
            "default_resolution": "720p", "default_ratio": "9:16", "default_duration": 6,
            "provider": "runninghub", "model": SCENE_MODEL}


def _reference_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 20000:
        raise HTTPException(422, "RunningHub 首尾帧需要有效的 HTTPS 图片地址")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        valid = (parsed.scheme == "https" and host and not parsed.username and not parsed.password
                 and not parsed.fragment and parsed.port in (None, 443)
                 and host != "localhost" and not host.endswith((".localhost", ".local"))
                 and not any(character.isspace() for character in value)
                 and not any(name.lower() in {"key", "apikey", "api_key", "authorization", "access_token"}
                             for name, _value in parse_qsl(parsed.query)))
        try:
            if not ipaddress.ip_address(host).is_global:
                valid = False
        except ValueError:
            pass
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(422, "RunningHub 首尾帧需要公开 HTTPS 图片地址；请先上传图片")
    return value


def _fingerprint(route: str, body: dict) -> str:
    canonical = json.dumps({"provider": "runninghub", "model": SCENE_MODEL, "route": route,
                            "body": body}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_scene_spec(scene: dict, plan: dict) -> dict[str, Any]:
    duration = scene.get("duration")
    resolution, ratio = plan.get("resolution", "720p"), plan.get("ratio", "9:16")
    if type(duration) is not int or not 4 <= duration <= 15:
        raise HTTPException(422, "RunningHub Mini 单镜头时长须为 4～15 秒整数")
    if resolution not in ("480p", "720p") or ratio != "9:16":
        raise HTTPException(422, "当前 RunningHub Mini 支持 480p/720p、9:16 竖屏")
    prompt = scene.get("seedance_prompt") or scene.get("visual_intent") or scene.get("prompt")
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 20480:
        raise HTTPException(422, "请先确认有效的分镜画面提示词")
    body = {"prompt": prompt.strip(), "resolution": resolution, "duration": str(duration),
            "ratio": ratio, "generateAudio": False, "returnLastFrame": False, "seed": -1}
    references = scene.get("references") or []
    if not isinstance(references, list) or len(references) > 2:
        raise HTTPException(422, "当前 RunningHub Mini 每镜最多使用首帧、尾帧各一张")
    frames = {}
    for reference in references:
        if not isinstance(reference, dict) or reference.get("role") not in ("first_frame", "last_frame"):
            raise HTTPException(422, "当前通道仅支持首帧/尾帧图片，请明确图片用途；通用参考图需使用多模态通道")
        role = reference["role"]
        if role in frames:
            raise HTTPException(422, "首帧和尾帧各只能选择一张图片")
        frames[role] = _reference_url(reference.get("data_url", reference.get("url")))
    if frames:
        if "first_frame" not in frames:
            raise HTTPException(422, "使用尾帧时还需要一张首帧图片")
        route = I2V_ROUTE
        body.update(firstFrameUrl=frames["first_frame"], realPersonMode=False)
        if "last_frame" in frames:
            body["lastFrameUrl"] = frames["last_frame"]
    else:
        route = T2V_ROUTE
        body["webSearch"] = False
    return {"provider": "runninghub", "model": SCENE_MODEL, "route": route, "body": body,
            "request_fingerprint": _fingerprint(route, body)}


def _checked_spec(spec: dict) -> dict:
    if (not isinstance(spec, dict) or spec.get("provider") != "runninghub"
            or spec.get("model") != SCENE_MODEL or spec.get("route") not in (T2V_ROUTE, I2V_ROUTE)
            or not isinstance(spec.get("body"), dict)):
        raise HTTPException(422, "RunningHub 生成规格不匹配，请重新确认方案")
    body = spec["body"]
    references = [{"role": role, "data_url": body[field]} for role, field in
                  (("first_frame", "firstFrameUrl"), ("last_frame", "lastFrameUrl")) if field in body]
    try:
        duration = int(body["duration"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "RunningHub 生成规格不匹配，请重新确认方案") from None
    rebuilt = build_scene_spec({"prompt": body.get("prompt"), "duration": duration, "references": references},
                               {"resolution": body.get("resolution"), "ratio": body.get("ratio")})
    if (rebuilt["route"] != spec["route"] or rebuilt["body"] != body
            or rebuilt["request_fingerprint"] != spec.get("request_fingerprint")):
        raise HTTPException(422, "RunningHub 已确认的参数与提交参数不一致，请重新确认方案")
    return rebuilt


def _credential() -> str:
    key = os.environ.get("RUNNINGHUB_API_KEY", "").strip()
    if not key:
        location = os.environ.get("RH_KEY_FILE", "").strip()
        try:
            key = Path(location).read_text(encoding="utf-8-sig").strip() if location else ""
        except (OSError, UnicodeError):
            key = ""
    if not key or len(key) > 4096 or any(character.isspace() for character in key):
        raise HTTPException(503, "RunningHub 凭证未配置或不可读取")
    return key


def _headers(key: str | None = None) -> dict[str, str]:
    return {"Authorization": "Bearer " + (key if key is not None else _credential()), "Content-Type": "application/json"}


def _numeric(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() and 0 <= amount <= Decimal("1000000000000") else None
    except (InvalidOperation, ValueError):
        return None


def _money(amount: Decimal) -> float:
    return float(amount.quantize(Decimal("0.0001"), rounding=ROUND_UP))


def quote_scene(spec: dict) -> dict[str, Any]:
    checked = _checked_spec(spec)
    route = checked["route"].replace("/openapi/v2/", "/openapi/v2/price-preview/", 1)
    key = _credential()
    cache_key = (hashlib.sha256(key.encode("utf-8")).hexdigest(), checked["request_fingerprint"])
    with _QUOTE_CACHE_LOCK:
        entry = _QUOTE_CACHE.get(cache_key)
        if entry is not None and entry["expires_mono"] > time.monotonic():
            _QUOTE_CACHE.move_to_end(cache_key)
            return deepcopy(entry["quote"])
        _QUOTE_CACHE.pop(cache_key, None)
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
            response = client.post(BASE_URL + route, headers=_headers(key), json=checked["body"])
            response.raise_for_status()
            result = response.json()
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(503, "RunningHub 官方估价暂不可用，请稍后重新获取费用") from None
    if not isinstance(result, dict) or result.get("errorCode") not in (None, "") or result.get("currency") not in ("CNY", "USD"):
        raise HTTPException(503, "RunningHub 未返回有效人民币或美元报价，暂不提交生成")
    amount = _numeric(result.get("estimatedPrice"))
    if amount is None:
        raise HTTPException(503, "RunningHub 未返回有效人民币或美元报价，暂不提交生成")
    now = datetime.now(timezone.utc)
    quote = {"amount": _money(amount), "reservation_amount": _money(amount * Decimal("1.2")),
            "currency": result["currency"], "provider": "runninghub", "model": SCENE_MODEL,
            "pricing_source": "runninghub_price_preview", "pricing_kind": "estimate", "source_url": SOURCE_URL,
            "verified_at": VERIFIED_AT, "quoted_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "request_fingerprint": checked["request_fingerprint"],
            "basis": ("RunningHub 官方接口按本次相同参数实时估价；软件另预留 20%，实际以供应商账单为准。"
                      + ("官方以美元报价，不代表人民币，未做换汇。" if result["currency"] == "USD" else "")
                      + ("官方本次估价为 0，不代表保证免费，仍须按实际账单核对。" if amount == 0 else "")),
            "provider_hard_cap": False}
    with _QUOTE_CACHE_LOCK:
        _QUOTE_CACHE[cache_key] = {"expires_mono": time.monotonic() + 300, "quote": deepcopy(quote)}
        _QUOTE_CACHE.move_to_end(cache_key)
        while len(_QUOTE_CACHE) > _QUOTE_CACHE_MAX:
            _QUOTE_CACHE.popitem(last=False)
    return quote


async def _post(route: str, body: dict, message: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
            response = await client.post(BASE_URL + route, headers=_headers(), json=body)
            response.raise_for_status()
            result = response.json()
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(502, message) from None
    if (not isinstance(result, dict)
            or (result.get("errorCode") not in (None, "") and result.get("status") != "FAILED")):
        raise HTTPException(502, message)
    return result


async def submit_scene(spec: dict) -> dict[str, Any]:
    checked = _checked_spec(spec)
    result = await _post(checked["route"], checked["body"], "RunningHub 提交未取得明确回执，请核对任务与账单后再操作")
    task_id = result.get("taskId")
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise HTTPException(502, "RunningHub 提交未取得明确回执，请核对任务与账单后再操作")
    statuses = {"QUEUED": "queued", "RUNNING": "running", "SUCCESS": "succeeded", "FAILED": "failed"}
    return {"id": task_id, "status": statuses.get(result.get("status"), "queued"), "submission_spec": checked,
            "provider": "runninghub", "model": SCENE_MODEL, "usage": _safe_usage(result.get("usage")),
            "currency": _safe_currency(result)}


def _safe_usage(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    fields = ("consumeMoney", "thirdPartyConsumeMoney", "consumeCoins", "taskCostTime")
    return {field: str(amount) for field in fields
            if (amount := _numeric(value.get(field))) is not None}


def _safe_currency(result: dict) -> str | None:
    value = result.get("currency")
    if value is None:
        return None
    return value if value in ("CNY", "USD", "RH") else "UNKNOWN"


async def query_scene(taskid: str) -> dict[str, Any]:
    if not isinstance(taskid, str) or not _TASK_ID.fullmatch(taskid):
        raise HTTPException(422, "RunningHub 任务编号无效")
    result = await _post("/openapi/v2/query", {"taskId": taskid}, "RunningHub 查询暂未成功，保留原任务继续查询")
    statuses = {"QUEUED": "queued", "RUNNING": "running", "SUCCESS": "succeeded", "FAILED": "failed"}
    status = statuses.get(result.get("status"), "failed")
    video = None
    for row in result.get("results") or []:
        if not isinstance(row, dict) or row.get("outputType") != "mp4":
            continue
        try:
            video = _reference_url(row.get("url"))
        except HTTPException:
            continue
        break
    return {"id": taskid, "status": status, "videoUrl": video,
            "usage": _safe_usage(result.get("usage")), "provider": "runninghub", "model": SCENE_MODEL,
            "currency": _safe_currency(result),
            "error": "RunningHub 生成未完成，请核对任务与账单" if status == "failed" else None}
