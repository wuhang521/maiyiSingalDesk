"""Controlled video adapters. Commands are fixed; HTTP clients never supply shell code.

All billable work requires current backend generation consent (or a legacy budget grant). Provider IDs are persisted
before polling/downloading, and ambiguous submissions are never automatically resent.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import ipaddress
import json
import math
import os
import re
import shutil
import socket
import subprocess
import wave
from decimal import Decimal, InvalidOperation, ROUND_UP
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException


KINDS = {"voice", "scene", "scene_batch", "avatar", "render"}
PLATFORMS = {"douyin": "抖音", "weixin": "视频号", "xiaohongshu": "小红书"}
SEEDANCE_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
SEEDANCE_MODEL = "doubao-seedance-2-0-mini-260615"
SEEDANCE_MEDIA_HOST = "ark-acg-cn-beijing.tos-cn-beijing.volces.com"
PROXY_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
LOCAL_REVIEW_TIMEOUT_SECONDS = 20


def _settings():
    return importlib.import_module("app.config").get_settings()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bundle() -> Path:
    return Path(_env("TASK_VIDEO_SKILL_DIR") or Path(__file__).resolve().parents[2] / "hermes-skill/oldx-avatar").resolve()


def _python() -> str:
    value = _env("TASK_VIDEO_PYTHON")
    return str(Path(value).resolve()) if value and Path(value).is_file() else ""


def _ffmpeg() -> str:
    return _discover_ffmpeg(_env("TASK_VIDEO_FFMPEG"), _python(), os.environ.get("PATH", ""))


@lru_cache(maxsize=8)
def _discover_ffmpeg(configured: str, python: str, path: str) -> str:
    if configured:
        return configured if Path(configured).is_file() else ""
    executable = shutil.which("ffmpeg", path=path)
    if executable:
        return executable
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        pass
    if python:
        try:
            probe = subprocess.run([python, "-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"],
                                   capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        candidate = probe.stdout.strip()
        if probe.returncode == 0 and Path(candidate).is_file():
            return candidate
    return ""


def runtime_capabilities() -> dict:
    settings = _settings()
    python_ready = bool(_python() and (_bundle() / "scripts/pipeline.py").is_file())
    paid_enabled = _env("TASK_VIDEO_PAID_ENABLED").lower() in ("1", "true", "yes")
    tts_ready = bool(_env("DASHSCOPE_API_KEY") or _env("BL_KEY_FILE"))
    voice_source = _env("TASK_VIDEO_VOICE_CREDENTIAL_SOURCE", "local" if tts_ready else "unconfigured")
    if voice_source not in {"hermes", "local", "unconfigured"}:
        voice_source = "local" if tts_ready else "unconfigured"
    rh_ready = bool(_env("RUNNINGHUB_API_KEY") or _env("RH_KEY_FILE"))
    ffmpeg_ready = bool(_ffmpeg())
    def cap(ready, reason, **extra):
        return {"ready": bool(ready), "reason": "" if ready else reason, **extra}
    scene_ready = bool(settings.seedance_api_key)
    scene_source = _env("TASK_VIDEO_SCENE_CREDENTIAL_SOURCE", "local" if scene_ready else "unconfigured")
    if scene_source not in {"hermes", "local", "unconfigured"}:
        scene_source = "local" if scene_ready else "unconfigured"
    official = (getattr(settings, "seedance_base_url", SEEDANCE_BASE_URL).rstrip("/") == SEEDANCE_BASE_URL
                and getattr(settings, "seedance_model", SEEDANCE_MODEL) == SEEDANCE_MODEL)
    scene_capability = cap(paid_enabled and scene_ready and official,
        "本地付费调用未启用" if not paid_enabled else "请配置 Seedance API 凭证" if not scene_ready else "普通视频需要火山官方北京 Seedance 2.0 Mini 配置",
        provider="seedance", model=SEEDANCE_MODEL, base_url=SEEDANCE_BASE_URL,
        credentials_configured=scene_ready, credential_source=scene_source, paid_enabled=paid_enabled,
        constraints={"provider": "seedance", "model": SEEDANCE_MODEL, "duration_min": 4, "duration_max": 15,
                     "duration_step": 1, "resolutions": ["480p", "720p"], "ratios": ["9:16"],
                     "default_resolution": "720p", "default_ratio": "9:16", "default_duration": 6})
    if _env("TASK_VIDEO_SCENE_PROVIDER", "seedance") == "runninghub":
        from . import video_runninghub as rh
        shared = _avatar_billing()[0] == "SHARED" and _avatar_billing()[2]
        scene_capability = cap(paid_enabled and rh_ready and shared,
            "本地付费调用未启用" if not paid_enabled else "普通视频需要可用的 RunningHub 企业共享 Key",
            provider="runninghub", model=rh.SCENE_MODEL, credentials_configured=rh_ready,
            credential_source="runninghub_shared_key", credential_type=_avatar_billing()[0], currency=None, supported_currencies=["CNY", "USD"],
            paid_enabled=paid_enabled, constraints=rh.scene_constraints())
    elif _env("TASK_VIDEO_SCENE_PROVIDER", "seedance") != "seedance":
        scene_capability = cap(False, "普通视频生成通道无效，不能自动切换供应商", provider=_env("TASK_VIDEO_SCENE_PROVIDER"))
    return {
        "planning": cap(bool(settings.prompt_model and (settings.prompt_api_key or settings.seedance_api_key)), "请配置策划模型和 API 凭证"),
        "voice": cap(paid_enabled and python_ready and tts_ready, "本地付费调用未启用" if not paid_enabled else "请配置 TASK_VIDEO_PYTHON、Skill 依赖及百炼密钥", provider="oldx-bailian", credentials_configured=tts_ready, credential_source=voice_source, paid_enabled=paid_enabled),
        "scene": scene_capability,
        "scene_batch": dict(scene_capability),
        "avatar": cap(paid_enabled and python_ready and rh_ready, "本地付费调用未启用" if not paid_enabled else "请配置 TASK_VIDEO_PYTHON、Skill 依赖及 RunningHub 密钥", provider="oldx-runninghub", credential_type=_avatar_billing()[0], currency=_avatar_billing()[1]),
        "render": cap(ffmpeg_ready, "请配置 TASK_VIDEO_FFMPEG 或安装 imageio-ffmpeg", provider="local-ffmpeg"),
    }


def _scene(job, project):
    scenes = project.get("plan", {}).get("scenes", [])
    found = next((s for s in scenes if str(s.get("id")) == str(job.get("scene_id"))), None)
    if found is None:
        raise HTTPException(409, "未找到当前镜头")
    return found


def _narration(project):
    return "\n".join(str(s.get("narration", "")).strip() for s in project.get("plan", {}).get("scenes", []))


def _avatar_billing():
    """Credential class is explicit configuration, never inferred from a balance."""
    key_type = _env("TASK_VIDEO_RUNNINGHUB_KEY_TYPE").upper()
    currency = {"SHARED": "CNY", "CONSUMER": "RH", "PERSONAL": "RH"}.get(key_type, "UNKNOWN")
    configured = _env("TASK_VIDEO_AVATAR_CURRENCY").upper()
    return key_type or "UNKNOWN", currency, currency != "UNKNOWN" and (not configured or configured == currency)


def _scene_batch_ids(job, project):
    selected = job.get("scene_ids")
    if not isinstance(selected, list) or not selected or len(selected) != len(set(map(str, selected))):
        raise HTTPException(422, "镜头批次必须绑定不重复的镜头名单")
    known = {str(s.get("id")) for s in project.get("plan", {}).get("scenes", [])}
    if any(str(value) not in known for value in selected):
        raise HTTPException(409, "镜头批次包含已不在当前方案中的镜头")
    return list(map(str, selected))


def _avatar_preflight(project):
    voice = _job_result(project, "voice")
    data = voice.get("avatar_preflight")
    if not isinstance(data, dict) or data.get("duration_source") != "actual_voice" or not data.get("segments"):
        raise HTTPException(409, "先生成配音并完成真实音频分段，才能生成整条数字人口播")
    seconds = Decimal(str(data["total_seconds"]))
    segments = data["segments"]
    if not seconds.is_finite() or seconds <= 0 or data.get("segment_count") != len(segments):
        raise HTTPException(409, "数字人实际音频分段数据不完整")
    if any(not Decimal(str(s["duration"])).is_finite() or not 0 < Decimal(str(s["duration"])) <= 14.001 for s in segments):
        raise HTTPException(409, "数字人分段超过当前 Skill 的 14 秒上限")
    return data


def _scene_execution(job, project):
    return (job.get("execution") or project.get("_scene_execution")
            or (job.get("estimated_cost") or {}).get("execution")
            or ((job.get("budget_grant") or {}).get("estimated_cost") or {}).get("execution"))


def _scene_provider(job, project):
    execution = _scene_execution(job, project)
    recorded = {value for value in ((job.get("result") or {}).get("provider"),
        (job.get("estimated_cost") or {}).get("provider"), (job.get("budget_grant") or {}).get("provider"))
        if isinstance(value, str) and value in {"runninghub", "seedance"}}
    if execution:
        provider = execution.get("provider")
        if provider not in {"runninghub", "seedance"} or recorded - {provider}:
            raise HTTPException(409, "任务绑定的生成通道无效，请重新确认生成")
        return provider
    if recorded:
        if len(recorded) != 1:
            raise HTTPException(409, "任务回执通道不一致，请核对原任务")
        return recorded.pop()
    # This exact legacy receipt shape was emitted only by the Ark adapter.
    legacy = job.get("submission_spec") or (job.get("result") or {}).get("submission_spec") or {}
    if isinstance(legacy, dict) and legacy.get("no_video_input") is True and isinstance(legacy.get("model"), str) and legacy["model"] and not {"route", "body"} & legacy.keys():
        return "seedance"
    if job.get("provider_task_id") or job.get("submission_started") or job.get("status") == "submitting":
        raise HTTPException(409, "旧任务缺少可靠的供应商记录，请核对原回执；不能猜测通道或重新提交")
    if job.get("id"):
        if (job.get("budget_grant") or {}).get("generation_consent") is True:
            raise HTTPException(409, "旧任务缺少固定生成通道，请重新确认生成")
        # Original quote-mode jobs predate the RunningHub integration. They still
        # cannot make new calls after a deployment provider switch (_budget).
        return "seedance"
    return _env("TASK_VIDEO_SCENE_PROVIDER", "seedance")


def _cost_review_required():
    return _env("TASK_VIDEO_COST_REVIEW_REQUIRED", "1").lower() not in {"0", "false", "no"}


def prepare_job(job: dict, project: dict) -> dict:
    """Build the confirmed scope locally. No prices, key reads, or network I/O."""
    kind = job.get("kind")
    metadata = {"cost_review_required": False, "scope": "scene" if kind == "scene" else "scene_batch" if kind == "scene_batch" else "project"}
    if kind in {"scene", "scene_batch"}:
        provider = _scene_provider(job, project)
        ids = _scene_batch_ids(job, project) if kind == "scene_batch" else [str(job.get("scene_id"))]
        if provider == "runninghub":
            from . import video_runninghub as rh
            if _avatar_billing()[0] != "SHARED" or not _avatar_billing()[2]:
                raise HTTPException(503, "普通视频尚未配置 RunningHub 企业共享通道")
            specs = {identifier: rh.build_scene_spec(_scene({"scene_id": identifier}, project), project.get("plan", {})) for identifier in ids}
            execution = {"schema": "runninghub-scene/v1", "provider": provider, "model": rh.SCENE_MODEL, "scenes": specs}
        elif provider == "seedance":
            video = importlib.import_module("app.video")
            specs = {}
            for identifier in ids:
                scene = _scene({"scene_id": identifier}, project)
                payload = video.GenerationCreate(prompt=scene.get("seedance_prompt") or scene.get("visual_intent"),
                    duration=scene["duration"], resolution=project.get("plan", {}).get("resolution", "720p"), ratio="9:16",
                    references=scene.get("references", []))
                specs[identifier] = video._build_generation_spec(payload, _settings())
            execution = {"schema": "seedance-scene/v1", "provider": provider, "model": SEEDANCE_MODEL,
                         "base_url": SEEDANCE_BASE_URL, "route": "/contents/generations/tasks", "scenes": specs}
        else:
            raise HTTPException(503, "普通视频生成通道无效，不能自动切换供应商")
        return {**metadata, "provider": provider, "model": execution["model"], "execution": execution,
                "scene_ids": ids, "segment_count": len(ids),
                "duration_seconds": sum(float(_scene({"scene_id": identifier}, project)["duration"]) for identifier in ids)}
    if kind == "avatar":
        preflight = _avatar_preflight(project)
        return {**metadata, "provider": "oldx-runninghub", "segment_count": preflight["segment_count"],
                "duration_seconds": preflight["total_seconds"], "segments": preflight["segments"]}
    if kind == "voice":
        return {**metadata, "provider": "oldx-bailian"}
    if kind == "render":
        return {**metadata, "provider": "local-ffmpeg"}
    raise HTTPException(422, "不支持的制作任务")


def _runninghub_quote(job, project, metadata):
    from . import video_runninghub as rh
    if _avatar_billing()[0] != "SHARED" or not _avatar_billing()[2]:
        raise HTTPException(503, "普通视频需要已确认的 RunningHub 企业共享 Key；实际币种取官方模型报价")
    ids = _scene_batch_ids(job, project) if job["kind"] == "scene_batch" else [str(job.get("scene_id"))]
    pinned = _scene_execution(job, project)
    specs = {}
    rows = []
    for identifier in ids:
        if pinned:
            spec = pinned.get("scenes", {}).get(identifier)
            if not spec:
                raise HTTPException(409, "已确认的批次缺少当前镜头参数，请重新询价")
        else:
            spec = rh.build_scene_spec(_scene({"scene_id": identifier}, project), project.get("plan", {}))
        specs[identifier] = spec
        rows.append({**rh.quote_scene(spec), "scene_id": identifier,
                     "duration_seconds": float(_scene({"scene_id": identifier}, project)["duration"])})
    currencies = {row["currency"] for row in rows}
    if len(currencies) != 1 or not currencies.issubset({"CNY", "USD"}):
        raise HTTPException(503, "RunningHub 返回的镜头币种不一致，不能合并授权，请重新询价")
    currency = rows[0]["currency"]
    execution = {"schema": "runninghub-scene/v1", "provider": "runninghub", "model": rh.SCENE_MODEL, "currency": currency, "scenes": specs}
    fingerprint = hashlib.sha256(json.dumps(execution, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    first = rows[0]
    return {**metadata, **{key: first[key] for key in ("currency", "pricing_source", "pricing_kind", "source_url", "verified_at", "provider_hard_cap")},
            "provider": "runninghub", "model": rh.SCENE_MODEL, "route": first.get("route") or specs[ids[0]]["route"],
            "credential_type": "SHARED", "billing_currency_confirmed": True,
            "amount": round(sum(row["amount"] for row in rows), 4),
            "reservation_amount": round(sum(row["reservation_amount"] for row in rows), 4),
            "quoted_at": min(row["quoted_at"] for row in rows), "expires_at": min(row["expires_at"] for row in rows),
            "basis": f"RunningHub 模型 API 对本次相同参数返回的 {'美元（USD）' if currency == 'USD' else '人民币（CNY）'}估价；另加 20% 同币种软件预留。未换算汇率。普通视频与数字人共用企业共享 Key，计费单位分别按各自官方报价，最终以 RunningHub 账单为准。",
            "budget_notice": "本次报价只覆盖列明的镜头、模型和参数。预留不是云端硬上限；已提交任务可能继续计费，预算不足将停止后续新提交。重做需重新确认。",
            "duration_seconds": sum(row["duration_seconds"] for row in rows), "segment_count": len(ids),
            "segments": [{key: row[key] for key in ("scene_id", "duration_seconds", "amount", "reservation_amount")} for row in rows],
            **({"scene_ids": ids} if job["kind"] == "scene_batch" else {}),
            "execution": execution, "request_fingerprint": fingerprint}


def estimate_job(job: dict, project: dict) -> dict:
    kind = job.get("kind")
    basis = ""
    amount = None
    currency = "CNY"
    metadata = {"scope": "scene" if kind == "scene" else "scene_batch" if kind == "scene_batch" else "project",
                "project_id": project.get("id"), "scene_id": job.get("scene_id") if kind == "scene" else None}
    try:
        if kind in {"scene", "scene_batch"} and _scene_provider(job, project) == "runninghub":
            metadata.update(provider="runninghub", credential_type="SHARED", pricing_source="runninghub_price_preview",
                            source_url="https://www.runninghub.cn/runninghub-api-doc-cn/api-454850620")
            return _runninghub_quote(job, project, metadata)
        if kind != "render" and _env("TASK_VIDEO_PRICING_PROFILE") == "official-cn-20261022":
            return _official_quote(job, project, metadata)
        if kind == "render":
            amount, basis = Decimal(0), "本地合成不调用付费供应商"
        elif kind == "scene":
            price = _env("TASK_VIDEO_SCENE_COST_PER_SECOND")
            if price:
                amount = Decimal(price) * Decimal(str(_scene(job, project)["duration"]))
            basis = "部署配置的 Seedance 每秒价格；需与实际账号和规格一致"
            metadata.update(duration_seconds=float(_scene(job, project)["duration"]), duration_source="planned_scene")
        elif kind == "scene_batch":
            ids = _scene_batch_ids(job, project)
            seconds = sum(Decimal(str(_scene({"scene_id": identifier}, project)["duration"])) for identifier in ids)
            price = _env("TASK_VIDEO_SCENE_COST_PER_SECOND")
            if price:
                amount = Decimal(price) * seconds
            basis = "仅覆盖本批次固定镜头名单；部署配置的 Seedance 每秒上限价格"
            metadata.update(scene_ids=ids, duration_seconds=float(seconds), duration_source="planned_scenes", segment_count=len(ids))
        elif kind == "voice":
            price = _env("TASK_VIDEO_VOICE_COST_PER_1000_CHARS")
            if price:
                amount = Decimal(price) * Decimal(len(_narration(project))) / 1000
            basis = "部署配置的配音与逐词识别合并每千字上限价格"
        elif kind == "avatar":
            key_type, currency, billing_valid = _avatar_billing()
            metadata.update(credential_type=key_type, billing_currency_confirmed=billing_valid)
            preflight = _avatar_preflight(project)
            seconds = Decimal(str(preflight["total_seconds"]))
            price = _env("TASK_VIDEO_AVATAR_COST_PER_SECOND")
            amount = seconds * Decimal(price) if price and billing_valid else None
            metadata.update(duration_seconds=float(seconds), duration_source="actual_voice", segment_count=preflight["segment_count"],
                            segments=preflight["segments"], voice_audio_path=preflight.get("voice_audio_path"))
            basis = ("整条数字人口播；部署者明确配置的每秒输出预算上限，必须覆盖当前工作流和实例的真实费用；"
                     "企业共享 Key 以 CNY 核算，输出秒数不代表 GPU 计费秒数")
            if not billing_valid:
                basis = "RunningHub Key 类型未知或币种配置冲突；请确认计费币种后配置可靠上限报价"
        if amount is not None and (not amount.is_finite() or amount < 0):
            amount = None
    except HTTPException as error:
        amount, basis = None, str(error.detail)
    except (InvalidOperation, ValueError, KeyError, TypeError):
        amount = None
    return {"amount": float(amount.quantize(Decimal("0.0001"), rounding=ROUND_UP)) if amount is not None else None,
            "currency": currency, "basis": basis or "未配置此操作的可靠报价", **metadata}


def _official_quote(job, project, metadata):
    """Public list prices plus explicit application reservations, never hard caps."""
    from .video_pricing import official_scene_quote
    kind = job.get("kind")
    common = {**metadata, "currency": "CNY", "verified_at": "2026-10-22", "provider_hard_cap": False,
              "budget_notice": "供应商按实际用量结算，预留金额不是云端硬上限。客户端逐段检查预算；已提交任务仍可能继续计费。重做须重新确认。"}
    if kind in {"scene", "scene_batch"}:
        settings = _settings()
        base = urlparse(getattr(settings, "seedance_base_url", "https://ark.cn-beijing.volces.com/api/v3"))
        if (getattr(settings, "seedance_model", "") != "doubao-seedance-2-0-mini-260615"
                or base.scheme != "https" or base.hostname != "ark.cn-beijing.volces.com" or base.username or base.password or base.port not in (None, 443)):
            return {**common, "amount": None, "basis": "当前视频模型或服务地址与已核验的北京 Ark 官方 Mini 价表不符"}
        ids = _scene_batch_ids(job, project) if kind == "scene_batch" else [job.get("scene_id")]
        rows = [official_scene_quote(_scene({"scene_id": key}, project)["duration"],
                                    project.get("plan", {}).get("resolution", "720p"), "9:16") for key in ids]
        return {**common, "amount": round(sum(row["amount"] for row in rows), 4),
                "reservation_amount": round(sum(row["reservation_amount"] for row in rows), 4),
                "pricing_kind": "estimate", "source_url": rows[0]["source_url"], "model": rows[0]["model"],
                "basis": "Seedance 2.0 Mini 无输入视频：23 元/百万输出 Token；按分辨率、24 fps 和时长估算。预算另按每镜多 1 秒并加 20% 预留，不计账号折扣。",
                "duration_seconds": sum(float(_scene({"scene_id": key}, project)["duration"]) for key in ids),
                "duration_source": "planned_scenes", "segment_count": len(ids),
                **({"scene_ids": ids} if kind == "scene_batch" else {})}
    if kind == "voice":
        # This is an application allowance, not an undocumented audio-token ratio.
        # Keep the policy visible and reconcile every HTTP request using real usage.
        reserve = max(Decimal("1"), Decimal(len(_narration(project))) / 200)
        reserve = reserve.quantize(Decimal("0.01"), rounding=ROUND_UP)
        return {**common, "amount": float(reserve), "reservation_amount": float(reserve), "pricing_kind": "reservation",
                "model": "qwen-audio-3.1-tts-flash + qwen-audio-3.1-asr-flash",
                "source_url": "https://help.aliyun.com/zh/model-studio/model-pricing",
                "basis": "千问北京地域官方价：TTS 输入 1.5、输出 12 元/百万 Token；ASR 输入 0.8、输出 2.7 元/百万 Token。此处仅为软件预留：每 200 字 1 元、至少 1 元；不是官方按字定价或精确预测，实际按返回 Token 对账。"}
    if kind == "avatar":
        key_type, currency, valid = _avatar_billing()
        if not valid or key_type != "SHARED":
            return {**common, "currency": currency, "credential_type": key_type, "amount": None,
                    "basis": "当前官方价表适用企业共享 Key；其他 Key 类型须另配对应计费规则"}
        preflight = _avatar_preflight(project)
        # Public billing is GPU runtime, unrelated to output seconds. A generous
        # local allowance is reviewable without inventing an official fixed quote.
        segment_reserve = Decimal("6") * 600 / 3600
        reserve = segment_reserve * preflight["segment_count"]
        return {**common, "amount": float(reserve), "reservation_amount": float(reserve), "pricing_kind": "reservation",
                "credential_type": key_type, "billing_currency_confirmed": True, "gpu_hourly_cny": 6,
                "runtime_reservation_seconds_per_segment": 600, "segment_reservation_cny": float(segment_reserve),
                "source_url": "https://www.runninghub.cn/call-api/bill-task?tab=keys&type=shared",
                "basis": "RunningHub 企业共享 Plus 48GB 官方价 6 元/GPU 小时，按累计运行秒数计费。软件暂按每段 10 分钟运行时间预留 1 元；不代表实际需时，也不按输出视频秒数收费。",
                "duration_seconds": preflight["total_seconds"], "duration_source": "actual_voice",
                "segment_count": preflight["segment_count"], "segments": preflight["segments"],
                "voice_audio_path": preflight.get("voice_audio_path")}
    return {**common, "amount": None, "basis": "此操作没有已核验价表"}


def _review(project, stage):
    record = project.get("reviews", {}).get(stage, {})
    if record.get("revision") != project.get("revision") or record.get("decision") not in ("approved", "skipped") or not str(record.get("quote", "")).strip():
        raise HTTPException(409, f"当前方案的 {stage} 阶段等待真实用户确认")
    return record


def _avatar_spec(project):
    avatar = project.get("plan", {}).get("avatar", {})
    if not isinstance(avatar, dict) or avatar.get("image_use_confirmed") is not True:
        raise HTTPException(409, "数字人图片的使用授权尚未由用户明确确认")
    if not isinstance(avatar, dict) or any(not isinstance(avatar.get(key), str) or not avatar[key].strip()
            for key in ("image_type", "hands", "posture", "motion")):
        raise HTTPException(409, "数字人方案须先明确并展示图片类型、双手姿态、身体姿态和动作提示词，再确认生成；不能使用隐藏默认动作")
    return avatar


def _budget(job, project):
    if (job.get("kind") in {"scene", "scene_batch"} and job.get("id")
            and _scene_provider(job, project) != _env("TASK_VIDEO_SCENE_PROVIDER", "seedance")):
        raise HTTPException(409, "生成通道已切换；旧任务仅可查询已提交回执，未提交镜头请重新确认生成")
    grant = job.get("budget_grant") or {}
    if not _cost_review_required():
        if (grant.get("approved") is not True or grant.get("generation_consent") is not True
                or grant.get("revision") != project.get("revision")):
            raise HTTPException(409, "当前版本尚未取得用户点击生成的确认")
        return
    quote = estimate_job(job, project)
    if quote["amount"] is None:
        raise HTTPException(503, "未配置可靠报价，不能提交付费生成")
    if not grant.get("approved") or grant.get("revision") != project.get("revision"):
        raise HTTPException(409, "当前方案尚未取得付费预算授权")
    try:
        maximum = Decimal(str(grant["max_cost"]))
        required = quote.get("reservation_amount", quote["amount"])
        if not maximum.is_finite() or maximum < Decimal(str(required)) or grant.get("currency") != quote["currency"]:
            raise ValueError()
    except (KeyError, ValueError, InvalidOperation):
        raise HTTPException(409, "预算金额或币种不足以覆盖当前操作")


def validate_job(job: dict, project: dict) -> None:
    kind = job.get("kind")
    if kind not in KINDS:
        raise HTTPException(422, "不支持的制作任务")
    if job.get("revision") != project.get("revision"):
        raise HTTPException(409, "任务方案已过期")
    capabilities = runtime_capabilities()
    if kind in {"scene", "scene_batch"} and _scene_provider(job, project) != _env("TASK_VIDEO_SCENE_PROVIDER", "seedance"):
        enabled = _env("TASK_VIDEO_PAID_ENABLED").lower() in {"1", "true", "yes"}
        provider = _scene_provider(job, project)
        configured = bool(_settings().seedance_api_key) if provider == "seedance" else bool(_env("RUNNINGHUB_API_KEY") or _env("RH_KEY_FILE"))
        capabilities[kind] = {"ready": enabled and configured, "reason": "旧任务需要原供应商凭证以继续查询，不会切换生成通道"}
    if not capabilities[kind]["ready"]:
        raise HTTPException(503, capabilities[kind]["reason"])
    _review(project, "inputs")
    if kind in ("scene", "scene_batch", "avatar", "render"):
        _review(project, "voice")
        _review(project, "generation")
    if kind != "render" and not job.get("provider_task_id"):
        _budget(job, project)
    if kind in ("scene", "scene_batch"):
        for identifier in (_scene_batch_ids(job, project) if kind == "scene_batch" else [job.get("scene_id")]):
            scene = _scene({"scene_id": identifier}, project)
            if not 4 <= int(scene.get("duration", 0)) <= 15:
                raise HTTPException(422, "Seedance 单镜头时长必须为 4–15 秒")
            if not (scene.get("seedance_prompt") or scene.get("visual_intent")):
                raise HTTPException(422, "镜头缺少画面提示词")
    if project.get("mode") == "avatar" and kind in ("voice", "avatar") and not job.get("provider_task_id"):
        _avatar_spec(project)
    if kind == "render" and project.get("mode") == "avatar" and _postproduction(project)["trim_pauses"]:
        _postprocess_review(project)


async def _notify(callback, **data):
    if callback:
        response = callback(data)
        if inspect.isawaitable(response):
            await response


def _safe_path(root: Path, relative: str, *, exists=True) -> Path:
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or (exists and not target.is_file()):
        raise HTTPException(409, "项目媒体不存在或超出项目目录")
    return target


def _relative(root, path):
    return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")


async def _download(url: str, destination: Path):
    """Provider media only: reject local/private addresses, including each redirect."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    async with httpx.AsyncClient(timeout=120, follow_redirects=False, trust_env=False) as client:
        for _ in range(4):
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise RuntimeError("供应商媒体地址不符合 HTTPS 要求")
            addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, 443, 0, socket.SOCK_STREAM)
            # Some local proxies resolve this exact official CDN to fake-IP.
            # Keep HTTPS/TLS verification and reject every other non-public IP,
            # including mixed DNS answers and each subsequent redirect target.
            official_fake_ip = parsed.hostname == SEEDANCE_MEDIA_HOST and parsed.port in (None, 443)
            if not addresses or any(not (address.is_global or official_fake_ip and address in PROXY_FAKE_IP_NETWORK)
                                    for address in (ipaddress.ip_address(row[4][0]) for row in addresses)):
                raise RuntimeError("拒绝访问本地或私有网络媒体地址")
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    from urllib.parse import urljoin
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                size = 0
                with temporary.open("wb") as stream:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 1024 * 1024 * 1024:
                            raise RuntimeError("供应商视频超过 1 GB 限制")
                        stream.write(chunk)
                temporary.replace(destination)
                return
    raise RuntimeError("供应商媒体重定向过多")


async def _scene_job(job, project, root, callback):
    if _scene_provider(job, project) == "runninghub":
        return await _runninghub_scene_job(job, project, root, callback)
    video = importlib.import_module("app.video")
    task_id = job.get("provider_task_id")
    spec = job.get("submission_spec") or (job.get("result") or {}).get("submission_spec") or {}
    execution = _scene_execution(job, project)
    pinned = (execution or {}).get("scenes", {}).get(str(job.get("scene_id")))
    if execution:
        if (execution.get("schema"), execution.get("provider"), execution.get("model"), execution.get("base_url")) != ("seedance-scene/v1", "seedance", SEEDANCE_MODEL, SEEDANCE_BASE_URL) or not pinned:
            raise HTTPException(409, "Seedance 任务缺少固定生成参数，请重新确认生成")
        pinned = video._checked_generation_spec(pinned)
        spec = {"provider": "seedance", "model": pinned["model"], "base_url": pinned["base_url"],
                "request_fingerprint": pinned["request_fingerprint"], "no_video_input": True}
    metadata = {"provider": "seedance", "submission_spec": spec}
    if not task_id:
        if job.get("submission_started") or job.get("status") == "submitting":
            return {"status": "blocked", "result": metadata, "error": "上次提交结果不明确，请先在供应商后台核对，不能自动重复付费提交"}
        scene = _scene(job, project)
        await _notify(callback, status="submitting", submission_started=True, result=metadata)
        try:
            created = await video._submit_generation(video.GenerationCreate(
                prompt=scene.get("seedance_prompt") or scene["visual_intent"], duration=int(scene["duration"]),
                resolution=project.get("plan", {}).get("resolution", "720p"), ratio="9:16", references=scene.get("references", [])),
                **({"execution_spec": pinned} if pinned else {}))
        except Exception:
            return {"status": "blocked", "result": metadata, "error": "供应商提交未获得明确回执，请核对后台；不能自动重复提交"}
        task_id = created["id"]
        spec = spec or {"model": getattr(_settings(), "seedance_model", ""), "no_video_input": True}
        metadata["submission_spec"] = spec
        await _notify(callback, status="queued", provider_task_id=task_id, submission_started=True, result=metadata)
    try:
        queried = await video._query_generation(task_id, **({"execution_spec": pinned} if pinned else {}))
    except Exception:
        return {"status": "queued", "provider_task_id": task_id, "result": metadata, "error": "供应商查询暂未成功，保留原任务继续查询"}
    status = queried.get("status", "failed")
    from .video_pricing import actual_scene_cost
    expected_model = "doubao-seedance-2-0-mini-260615"
    matches = (queried.get("model") == expected_model or spec.get("model") == expected_model and spec.get("no_video_input") is True)
    if queried.get("model") not in (None, expected_model):
        matches = False
    actual = actual_scene_cost(queried.get("usage")) if status == "succeeded" and matches else None
    billing = {"currency": "CNY", "known_cost": actual or 0, "complete": actual is not None,
               "source": "provider_usage", "usage": queried.get("usage") or {}, "unknown_requests": int(actual is None)}
    if status in {"succeeded", "failed"}:
        await _notify(callback, result={**metadata, "billing": billing})
    if status == "succeeded":
        url = queried.get("videoUrl")
        if not url:
            return {"status": "failed", "provider_task_id": task_id, "error": "供应商成功状态缺少视频地址"}
        destination = root / "scenes" / f"{_identifier(job['id'])}.mp4"
        if not destination.exists():
            try:
                await _download(url, destination)
            except Exception:
                return {"status": "queued", "provider_task_id": task_id, "result": {**metadata, "video_url": url, "billing": billing},
                        "error": "云端已完成，下载暂未成功；再次执行只下载，不重新提交"}
        return {"status": "succeeded", "provider_task_id": task_id,
                "result": {**metadata, "path": _relative(root, destination), "video_url": url, "duration": _scene(job, project)["duration"], "billing": billing}}
    return {"status": status if status in ("queued", "running", "failed") else "failed",
            "provider_task_id": task_id, "error": queried.get("error"),
            "result": {**metadata, **({"billing": billing} if status == "failed" else {})}}


def _runninghub_billing(queried, expected_currency):
    usage = queried.get("usage") or {}
    def amount(name):
        raw = usage.get(name)
        if raw is None or isinstance(raw, bool):
            return None
        try:
            value = Decimal(str(raw))
            return float(value) if value.is_finite() and value >= 0 else None
        except InvalidOperation:
            return None
    known = amount("consumeMoney")
    extra = amount("thirdPartyConsumeMoney")
    reported_currency = queried.get("currency") if queried.get("currency") in {"CNY", "USD"} else "UNKNOWN"
    currency_valid = reported_currency == expected_currency
    complete = known is not None and not extra and currency_valid
    result = {"currency": expected_currency, "known_cost": known if known is not None and currency_valid else 0,
              "complete": complete, "unknown_requests": int(not complete), "source": "provider_report"}
    if known is not None and not currency_valid:
        result.update(reported_amount=known, reported_currency=reported_currency)
        result["note"] = "供应商返回金额的币种未与本次报价核对；保留原始数值，不换汇、不记作零费用。"
    if extra is not None:
        if reported_currency == "CNY":
            result["third_party_reported_cny"] = extra
        else:
            result.update(third_party_reported_amount=extra, third_party_reported_currency=reported_currency)
    if extra:
        result["note"] = "第三方金额与 consumeMoney 的包含关系待核对，未直接相加；最终以 RunningHub 账单为准。"
    return result


async def _runninghub_scene_job(job, project, root, callback):
    from . import video_runninghub as rh
    execution = _scene_execution(job, project)
    spec = (execution or {}).get("scenes", {}).get(str(job.get("scene_id"))) or (job.get("result") or {}).get("submission_spec")
    task_id = job.get("provider_task_id")
    if not spec and not task_id and not job.get("submission_started") and job.get("status") != "submitting":
        raise HTTPException(409, "RunningHub 任务缺少确认过的模型与参数，请重新确认生成")
    result_meta = {"provider": "runninghub", "submission_spec": spec}
    expected_currency = (execution or {}).get("currency") or (job.get("budget_grant") or {}).get("currency", "CNY")
    if not task_id:
        if job.get("submission_started") or job.get("status") == "submitting":
            return {"status": "blocked", "result": result_meta, "error": "上次 RunningHub 提交结果不明确，请先核对云端任务，不能自动重复付费提交"}
        await _notify(callback, status="submitting", submission_started=True, result=result_meta)
        try:
            created = await rh.submit_scene(spec)
        except Exception:
            return {"status": "blocked", "result": result_meta, "error": "RunningHub 提交没有明确回执；已阻止自动重发，请核对云端任务和账单"}
        task_id = created["id"]
        if created.get("status") == "failed":
            result_meta["billing"] = _runninghub_billing(created, expected_currency)
            await _notify(callback, status="failed", provider_task_id=task_id, submission_started=True, result=result_meta)
            return {"status": "failed", "provider_task_id": task_id, "result": result_meta,
                    "error": "RunningHub 返回生成失败，请核对原任务和账单；重做需重新确认"}
        await _notify(callback, status="queued", provider_task_id=task_id, submission_started=True, result=result_meta)
    try:
        queried = await rh.query_scene(task_id)
    except Exception:
        return {"status": "queued", "provider_task_id": task_id, "result": result_meta,
                "error": "RunningHub 查询暂未成功，保留原任务号继续查询，不重新生成"}
    status = queried.get("status", "failed")
    if status in {"succeeded", "failed"}:
        result_meta["billing"] = _runninghub_billing(queried, expected_currency)
        await _notify(callback, result=result_meta)
    if status != "succeeded":
        return {"status": status if status in {"queued", "running", "failed"} else "failed",
                "provider_task_id": task_id, "result": result_meta, "error": queried.get("error")}
    url = queried.get("videoUrl")
    if not url:
        return {"status": "failed", "provider_task_id": task_id, "result": result_meta, "error": "RunningHub 完成回执缺少视频地址，请核对原任务"}
    result_meta["video_url"] = url
    destination = root / "scenes" / f"{_identifier(job['id'])}.mp4"
    if not destination.exists():
        try:
            await _download(url, destination)
        except Exception:
            return {"status": "queued", "provider_task_id": task_id, "result": result_meta,
                    "error": "RunningHub 已完成，下载暂未成功；继续原任务只会重试下载，不重新付费生成"}
    return {"status": "succeeded", "provider_task_id": task_id, "result": {**result_meta,
            "path": _relative(root, destination), "duration": _scene(job, project)["duration"]}}


async def _scene_batch_job(job, project, root, callback):
    """Persist every receipt locally and in the DB callback before advancing."""
    ids = _scene_batch_ids(job, project)
    ledger = root / "scenes" / f"{_identifier(job['id'])}-ledger.json"
    saved = job.get("result") or {}
    if ledger.is_file():
        local = json.loads(ledger.read_text(encoding="utf-8"))
        if local.get("scene_ids") != ids:
            raise HTTPException(409, "本地镜头批次名单与授权不一致")
        saved = local
    if saved.get("scene_ids") not in (None, ids):
        raise HTTPException(409, "镜头批次名单不能在提交后变化")
    children = {str(row["scene_id"]): dict(row) for row in saved.get("scene_results", []) if str(row.get("scene_id")) in ids}
    marker = f"batch-ledger:{_identifier(job['id'])}"
    if (job.get("submission_started") or job.get("status") == "submitting") and not children:
        return {"status": "blocked", "error": "镜头批次提交记录不完整，请核对供应商任务后再继续，不能自动重复提交"}

    async def persist():
        state = {"scene_ids": ids, "scene_results": [children[key] for key in ids if key in children]}
        state["billing"] = _sum_billing([row.get("billing") for row in state["scene_results"] if row.get("submission_started") or row.get("provider_task_id")])
        state["provider"] = _scene_provider(job, project)
        _dump(ledger, state)
        await _notify(callback, status="running", provider_task_id=marker, submission_started=True, result=state)
        return state

    for identifier in ids:
        current = children.setdefault(identifier, {"scene_id": identifier, "status": "queued"})
        if current.get("status") == "succeeded":
            _safe_path(root, current["path"])
            continue
        child_id = f"batch-{hashlib.sha256(str(job['id']).encode()).hexdigest()[:24]}-{ids.index(identifier) + 1}"
        child_job = {**job, **current, "id": child_id, "kind": "scene", "scene_id": identifier}
        # The aggregate ledger marker is not a provider task receipt for a child.
        child_job["provider_task_id"] = current.get("provider_task_id")
        child_job["submission_started"] = current.get("submission_started", False)
        # A batch receipt only authorizes polling existing children. Advancing to
        # an unsubmitted child still needs the current aggregate budget ceiling.
        if not child_job["provider_task_id"] and not child_job["submission_started"]:
            _budget(job, project)
            if _cost_review_required() and (_env("TASK_VIDEO_PRICING_PROFILE") == "official-cn-20261022" or _scene_provider(job, project) == "runninghub"):
                # Completed unknown charges keep their reservation occupied.
                # An already submitted child is always allowed to be polled.
                spent = Decimal(0)
                for key, row in children.items():
                    if key == identifier or not (row.get("submission_started") or row.get("provider_task_id")):
                        continue
                    bill = row.get("billing") or {}
                    reserve = Decimal(str(estimate_job({"kind": "scene", "scene_id": key, "execution": _scene_execution(job, project)}, project)["reservation_amount"]))
                    known_cost = Decimal(str(bill.get("known_cost", 0)))
                    spent += known_cost if bill.get("complete") else max(known_cost, reserve)
                remaining = sum(Decimal(str(estimate_job({"kind": "scene", "scene_id": key, "execution": _scene_execution(job, project)}, project)["reservation_amount"]))
                                for key in ids if key not in children or not (children[key].get("submission_started") or children[key].get("provider_task_id")))
                if spent + remaining > Decimal(str(job["budget_grant"]["max_cost"])):
                    return {"status": "blocked", "provider_task_id": marker, "result": await persist(),
                            "error": "已知费用及剩余预留超过本批预算；已停止后续镜头，请核对账单后重新确认缺失镜头"}
        async def update(data):
            current.update({key: value for key, value in data.items() if key != "result"})
            current.update(data.get("result") or {})
            await persist()
        outcome = await _scene_job(child_job, project, root, update)
        current.update({key: value for key, value in outcome.items() if key != "result"})
        current.update(outcome.get("result") or {})
        state = await persist()
        if outcome["status"] != "succeeded":
            return {"status": outcome["status"], "provider_task_id": marker, "result": state, "error": outcome.get("error")}
    return {"status": "succeeded", "provider_task_id": marker, "result": await persist()}


def _identifier(value):
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise HTTPException(422, "项目或任务标识无效")
    return value


def _dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _sum_billing(records):
    rows = [row if isinstance(row, dict) else {} for row in records]
    currencies = {row["currency"] for row in rows if row.get("currency") in {"CNY", "USD"}}
    mixed = len(currencies) > 1
    currency = "UNKNOWN" if mixed else next(iter(currencies)) if currencies else "CNY"
    source = "provider_report" if any(row.get("source") == "provider_report" for row in rows) else "provider_usage"
    extra = sum(float(row.get("third_party_reported_cny", 0)) for row in rows)
    return {"currency": currency, "known_cost": 0 if mixed else round(sum(float(row.get("known_cost", 0)) for row in rows), 8),
            "complete": bool(rows) and not mixed and all(row.get("complete") is True for row in rows),
            "unknown_requests": max(int(mixed), sum(int(row.get("unknown_requests", not row.get("complete"))) for row in rows)),
            "source": source, **({"reported_amount": rows[0]["reported_amount"], "reported_currency": rows[0].get("reported_currency", "UNKNOWN")} if len(rows) == 1 and rows[0].get("reported_amount") is not None else {}),
            **({"third_party_reported_cny": round(extra, 8),
                "note": "第三方金额包含关系待核，未与供应商返回金额直接相加。"} if extra else {})}


def _voice_billing(path):
    latest = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            latest[row["call_id"]] = row
    except FileNotFoundError:
        pass
    except (ValueError, KeyError, TypeError):
        return {"currency": "CNY", "known_cost": 0, "complete": False, "source": "provider_usage", "unknown_requests": 1}
    rows = []
    for row in latest.values():
        cost = row.get("cost_cny")
        known = row.get("state") == "measured" and cost is not None
        rows.append({"known_cost": float(cost) if known else 0, "complete": known, "unknown_requests": int(not known)})
    return _sum_billing(rows)


def _avatar_billing_result(wd, job):
    path = wd / "avatar/result.json"
    state = {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        records = [row for row in state.get("billing", {}).values() if row.get("budget_job_id") == str(job["id"])]
    except (OSError, ValueError, AttributeError):
        records = []
    rows, third_party = [], Decimal(0)
    for row in records:
        usage = row.get("usage") or {}
        try:
            value = Decimal(str(usage.get("consumeMoney")))
            known = value.is_finite() and value >= 0
        except InvalidOperation:
            value, known = Decimal(0), False
        try:
            extra = Decimal(str(usage.get("thirdPartyConsumeMoney", 0)))
            if not extra.is_finite() or extra < 0:
                extra = Decimal(0)
        except InvalidOperation:
            extra = Decimal(0)
        third_party += extra
        rows.append({"known_cost": float(value) if known else 0, "complete": known and not extra,
                     "unknown_requests": int(not known or bool(extra))})
    billed_tasks = {str(row.get("task")) for row in records}
    for pending in state.get("pending", {}).values():
        if pending.get("budget_job_id") not in (None, str(job["id"])):
            continue
        if str(pending.get("task")) not in billed_tasks:
            rows.append({"known_cost": 0, "complete": False, "unknown_requests": 1})
    result = _sum_billing(rows)
    result.update(source="provider_report", third_party_reported_cny=float(third_party))
    if third_party:
        result["note"] = "另有第三方金额，官方未明确是否已包含在 consumeMoney 内；未相加，需按供应商账单核对。"
    return result


def _child_env(root):
    environment = os.environ.copy()
    environment.update({"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "DH_WORKSPACE": str(root), "OLDX_HERMES": "1",
                        "OLDX_PAID_MAX_TRIES": "1", "OLDX_VOICE_USAGE_LEDGER": str(Path(root) / "voice-usage.jsonl"),
                        "OLDX_COST_REVIEW_REQUIRED": "1" if _cost_review_required() else "0"})
    return environment


async def _stop_local_review(process):
    """Bounded cleanup of this local review's own Windows redirector tree."""
    try:
        if process.returncode is None and os.name == "nt":
            taskkill = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe")
            try:
                killer = await asyncio.create_subprocess_exec(taskkill, "/PID", str(process.pid), "/T", "/F",
                    stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                try:
                    await asyncio.wait_for(killer.wait(), timeout=5)
                except TimeoutError:
                    try:
                        killer.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(killer.wait(), timeout=2)
                    except (TimeoutError, ProcessLookupError):
                        pass
            except OSError:
                pass
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except (TimeoutError, ProcessLookupError):
            pass


async def _cli(root, script, *args, watch=None, paid_environment=None):
    executable = _python()
    target = (_bundle() / "scripts" / script).resolve()
    if not target.is_relative_to(_bundle() / "scripts") or not target.is_file() or not executable:
        raise HTTPException(503, "受控 Skill 运行时不可用")
    environment = _child_env(root)
    environment.update(paid_environment or {})
    process = await asyncio.create_subprocess_exec(executable, "-B", "-X", "utf8", str(target), *map(str, args), "--json",
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(root), env=environment)
    communicate = asyncio.create_task(process.communicate())
    deadline = asyncio.timeout(LOCAL_REVIEW_TIMEOUT_SECONDS if script == "review.py" else None)
    try:
        # The sidecar reads its parent's private stdin pipe on another thread.
        # A child must never inherit that handle. Reviews are entirely local and
        # also need a wall-clock deadline; an expired step is never retried here.
        async with deadline:
            while not communicate.done():
                await asyncio.wait({communicate}, timeout=1)
                if watch:
                    await watch()
            stdout, _stderr = await communicate
    except BaseException:
        if script == "review.py":
            try:
                await _stop_local_review(process)
            except Exception:
                # Cleanup errors must not replace the sanitized local timeout.
                pass
            finally:
                communicate.cancel()
                await asyncio.gather(communicate, return_exceptions=True)
        elif process.returncode is None:
            # Preserve the existing paid-pipeline cancellation behavior.
            process.terminate()
            await process.wait()
        if deadline.expired():
            raise RuntimeError("Skill 本地确认步骤 review.py 超时，已停止；请检查本机运行环境，系统不会自动重试") from None
        raise
    result = {}
    for line in reversed(stdout.decode("utf-8", errors="replace").splitlines()):
        try:
            candidate = json.loads(line)
            if isinstance(candidate, dict):
                result = candidate
                break
        except ValueError:
            continue
    if process.returncode != 0 or result.get("ok") is False:
        # Provider output can contain user text or credentials; never expose raw logs.
        raise RuntimeError(f"Skill 步骤 {script} 未完成（退出码 {process.returncode}），保留项目后可继续核对")
    return result


async def _map_review(project, wd, stage, local_stage=None):
    review = _review(project, stage)
    target = local_stage or stage
    await _cli(wd, "review.py", "present", target, "--workdir", wd)
    await _cli(wd, "review.py", "skip" if review["decision"] == "skipped" else "approve", target,
               "--workdir", wd, "--user-approved", review["quote"])


def _skill_workdir(root, project):
    return root / "skill" / f"revision-{int(project['revision'])}"


async def _prepare_voice(project, root):
    wd = _skill_workdir(root, project)
    wd.mkdir(parents=True, exist_ok=True)
    text = _narration(project)
    if not text.strip():
        raise HTTPException(409, "方案缺少旁白")
    (wd / "copy.txt").write_text(text, encoding="utf-8")
    audio = project.get("plan", {}).get("audio_plan", {})
    voice_id = audio.get("voice_id") or _env("TASK_VIDEO_VOICE_ID", "yuxiaoyun_v3.1")
    _dump(wd / "voice.json", {"voice_id": voice_id, "source": "system_voice"})
    _dump(wd / "emotions.json", {"instruction": audio.get("narration_style", "清晰、平静地讲解，语速适中"), "carry": "off", "rules": []})
    avatar = project.get("plan", {}).get("avatar", {})
    if project.get("mode") == "avatar":
        image = avatar.get("image_path") or project.get("avatar_image_path")
        if not image:
            raise HTTPException(409, "数字人需要已确认的人物图片")
        source = _safe_path(root, image)
        assets = wd / "assets"
        assets.mkdir(exist_ok=True)
        portrait = assets / ("presenter" + source.suffix.lower())
        shutil.copy2(source, portrait)
        # Full image decoding/preparation is local and runs before any TTS charge.
        image_args = ["--image", portrait, "--workdir", wd]
        if avatar.get("mouth_box"):
            box = avatar["mouth_box"]
            image_args += ["--mouth-box", ",".join(map(str, box)) if isinstance(box, list) else str(box)]
        await _cli(wd, "avatar/prepare_image.py", *image_args)
    await _cli(wd, "review.py", "init", "--workdir", wd)
    await _map_review(project, wd, "inputs")
    return wd


async def _voice_job(job, project, root, callback):
    wd = _skill_workdir(root, project)
    if job.get("submission_started") or job.get("status") == "submitting":
        return {"status": "blocked", "error": "配音调用结果不明确，请核对缓存和供应商账单后显式重试"}
    try:
        wd = await _prepare_voice(project, root)
    except RuntimeError:
        return {"status": "blocked", "result": {"phase": "local_preparation", "paid_request_started": False},
                "error": "本地配音准备未完成，尚未调用配音模型；请重试"}
    # Keep the job ledger above the nested skill directory: its UUID filename can
    # otherwise exceed Windows MAX_PATH before the first provider request.
    usage_file = root / f"voice-usage-{_identifier(job['id'])}.jsonl"
    try:
        # No billing row is invented and an existing ledger is never truncated.
        os.close(os.open(usage_file, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600))
    except OSError:
        return {"status": "blocked", "result": {"phase": "local_preparation", "paid_request_started": False},
                "error": "本地配音记录路径不可写，尚未调用配音模型；请重试"}
    await _notify(callback, status="submitting", submission_started=True)
    paid = {"OLDX_VOICE_USAGE_LEDGER": str(usage_file), "OLDX_VOICE_BUDGET_CNY": str(job.get("budget_grant", {}).get("max_cost", "")) if _cost_review_required() else ""}
    try:
        await _cli(wd, "pipeline.py", "voice", "--workdir", wd, "--yes", paid_environment=paid)
    except RuntimeError:
        return {"status": "blocked", "result": {"billing": _voice_billing(usage_file)},
                "error": "配音未完成，已停止新请求；请核对本次用量和缓存后重新确认，系统不会自动重复扣费提交"}
    audio, words = wd / "out/merged.wav", wd / "out/words.json"
    if not audio.is_file() or not words.is_file():
        raise RuntimeError("配音流程没有产出真实音频和逐词时间")
    with wave.open(str(audio), "rb") as stream:
        duration = stream.getnframes() / stream.getframerate()
    result = {"audio_path": _relative(root, audio), "words_path": _relative(root, words),
              "duration": round(duration, 3), "review_stage": "voice", "billing": _voice_billing(usage_file)}
    if project.get("mode") == "avatar":
        # This step is local only: it measures pauses/word boundaries and produces
        # the exact segment files that will later be submitted to RunningHub.
        await _cli(wd, "pipeline.py", "segments", "--workdir", wd)
        result["avatar_preflight"] = _capture_avatar_preflight(wd, root)
    return {"status": "succeeded", "result": result}


def _audio_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def _capture_avatar_preflight(wd, root):
    plan_path = wd / "avatar/plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    audio = wd / "out/merged.wav"
    with wave.open(str(audio), "rb") as stream:
        measured = stream.getnframes() / stream.getframerate()
    segments = [{"index": int(row["i"]), "start": float(row["start"]), "end": float(row["end"]), "duration": float(row["duration"])} for row in plan["segments"]]
    if not segments or abs(float(plan["total_seconds"]) - measured) > .01 or plan.get("audio_sha") != _audio_sha(audio):
        raise HTTPException(409, "数字人分段与真实配音不匹配，请重新完成配音与分段")
    cursor = 0.0
    for index, row in enumerate(segments, 1):
        if any(not math.isfinite(row[key]) for key in ("start", "end", "duration")) or row["index"] != index or abs(row["start"] - cursor) > .01 or not 0 < row["duration"] <= 14.001 or abs(row["end"] - row["start"] - row["duration"]) > .01:
            raise HTTPException(409, "数字人分段不连续或超过当前 Skill 上限")
        cursor = row["end"]
    if abs(cursor - measured) > .01:
        raise HTTPException(409, "数字人分段未覆盖整条配音")
    return {"scope": "project", "duration_source": "actual_voice", "total_seconds": round(measured, 3),
            "segment_count": len(segments), "segments": segments, "audio_sha": plan["audio_sha"],
            "voice_audio_path": _relative(root, audio), "voice_words_path": _relative(root, wd / "out/words.json"),
            "plan_path": _relative(root, plan_path)}


async def _avatar_job(job, project, root, callback):
    wd = _skill_workdir(root, project)
    if not (wd / "out/merged.wav").is_file():
        raise HTTPException(409, "先完成当前版本的配音并试听确认")
    result_file = wd / "avatar/result.json"
    saved = json.loads(result_file.read_text(encoding="utf-8")) if result_file.is_file() else {}
    # The subprocess writes a receipt immediately after submit. Recover it even if
    # it died before its normal result.json pending record was flushed.
    for receipt_file in (wd / "avatar").glob("submission_*.json"):
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        key = str(receipt.get("segment"))
        bill = saved.get("billing", {}).get(str(receipt.get("task"))) or {}
        if bill.get("status") == "FAILED":
            # Explicitly authorized new batches may replace a terminal failure.
            # Retain the old receipt instead of endlessly resurrecting its task.
            if not job.get("provider_task_id") and not job.get("submission_started"):
                archive = receipt_file.parent / "old"
                archive.mkdir(exist_ok=True)
                target = archive / f"{receipt_file.stem}-{hashlib.sha256(str(receipt.get('task')).encode()).hexdigest()[:12]}.json"
                receipt_file.replace(target)
            continue
        if receipt.get("task") and key not in saved.get("results", {}) and key not in saved.get("pending", {}):
            saved.setdefault("pending", {})[key] = {"task": receipt["task"], "instance": "plus", "seed": 42}
            _dump(result_file, saved)
    pending = saved.get("pending", {})
    resume_only = not _cost_review_required() and job.get("budget_grant", {}).get("generation_consent") is not True
    def remaining_legacy_segments():
        current = json.loads(result_file.read_text(encoding="utf-8")) if result_file.is_file() else {}
        plan = json.loads((wd / "avatar/plan.json").read_text(encoding="utf-8"))
        missing = [segment["i"] for segment in plan["segments"]
                   if not Path((current.get("results", {}).get(str(segment["i"])) or {}).get("file", "")).is_file()]
        return {"status": "failed", "result": {"skill_workdir": _relative(root, wd), "remaining_segments": missing,
                "needs_new_generation_consent": True, "billing": _avatar_billing_result(wd, job)},
                "error": "旧任务已提交的片段已结束；剩余片段请再次确认并创建新的生成任务"}
    if resume_only and not pending and saved.get("results"):
        if remaining_legacy_segments()["result"]["remaining_segments"]:
            return remaining_legacy_segments()
    if (job.get("submission_started") or job.get("status") == "submitting") and not pending and not saved.get("results"):
        return {"status": "blocked", "error": "数字人提交结果不明确，请先核对 RunningHub 任务"}
    if not pending and not saved.get("results"):
        avatar = _avatar_spec(project)
        assets = list((wd / "assets").glob("presenter.*"))
        if not assets:
            raise HTTPException(409, "缺少已确认的人物图")
        image_args = ["--image", assets[0], "--workdir", wd]
        if avatar.get("mouth_box"):
            box = avatar["mouth_box"]
            image_args += ["--mouth-box", ",".join(map(str, box)) if isinstance(box, list) else str(box)]
        await _cli(wd, "avatar/prepare_image.py", *image_args)
        await _cli(wd, "pipeline.py", "segments", "--workdir", wd)
        plan = json.loads((wd / "avatar/plan.json").read_text(encoding="utf-8"))
        actual, quoted = _capture_avatar_preflight(wd, root), _avatar_preflight(project)
        if any(actual.get(key) != quoted.get(key) for key in ("audio_sha", "total_seconds", "segments", "segment_count")):
            raise HTTPException(409, "实际配音分段已变化，请重新试听并确认整条数字人的生成范围")
        _dump(wd / "avatar/prompt_src.json", {"image_type": avatar["image_type"],
            "hands": avatar["hands"], "posture": avatar["posture"],
            "lines": [s["text_asr"] for s in plan["segments"]], "motions": [avatar["motion"]] * len(plan["segments"])})
        await _cli(wd, "avatar/build_prompts.py", "--workdir", wd)
        await _map_review(project, wd, "inputs")
        await _map_review(project, wd, "voice")
        await _map_review(project, wd, "generation")
    elif (not resume_only and not pending and saved.get("results") and not job.get("provider_task_id")
          and not job.get("submission_started")
          and job.get("budget_grant", {}).get("generation_consent") is True
          and str(job["id"]) not in saved.get("generation_authorizations", {})):
        # A new explicit generation click may continue missing pieces of a prior
        # finished batch. A refresh of an already submitted job cannot remap it.
        await _map_review(project, wd, "inputs")
        await _map_review(project, wd, "voice")
        await _map_review(project, wd, "generation")
    last_ids = None
    def task_reference(ids):
        value = json.dumps(ids, sort_keys=True)
        return value if len(value) < 480 else f"oldx-ledger:revision-{project['revision']}"
    async def watch():
        nonlocal last_ids
        try:
            current = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        ids = {str(k): v["task"] for k, v in current.get("pending", {}).items() if v.get("task")}
        if ids and ids != last_ids:
            last_ids = ids
            await _notify(callback, status="running", provider_task_id=task_reference(ids),
                          result={"provider_tasks": ids, "skill_workdir": _relative(root, wd)})
    await _notify(callback, status="submitting" if not pending else "running", submission_started=True)
    try:
        avatar_quote = estimate_job(job, project) if _cost_review_required() else {}
        await _cli(wd, "avatar/run_avatar.py", "--workdir", wd, "--yes", "--retries", "0", "--timeout-min", "1", watch=watch,
                   paid_environment={"OLDX_AVATAR_BUDGET_CNY": str(job.get("budget_grant", {}).get("max_cost", "")) if _cost_review_required() else "",
                                     "OLDX_AVATAR_SEGMENT_RESERVE_CNY": str(avatar_quote.get("segment_reservation_cny", 1)) if _cost_review_required() else "",
                                     "OLDX_AVATAR_GENERATION_CONSENT": "1" if not _cost_review_required() and job.get("budget_grant", {}).get("generation_consent") is True else "0",
                                      "OLDX_AVATAR_RESUME_ONLY": "1" if resume_only else "0",
                                     "OLDX_AVATAR_BUDGET_JOB_ID": str(job["id"])})
    except RuntimeError:
        await watch()
        saved = json.loads(result_file.read_text(encoding="utf-8")) if result_file.is_file() else {}
        if saved.get("pending"):
            ids = {str(k): v["task"] for k, v in saved["pending"].items() if v.get("task")}
            return {"status": "running", "provider_task_id": task_reference(ids),
                    "result": {"skill_workdir": _relative(root, wd), "provider_tasks": ids, "billing": _avatar_billing_result(wd, job)}, "error": "云端处理中，保留原任务继续查询"}
        if resume_only:
            return remaining_legacy_segments()
        return {"status": "blocked", "result": {"billing": _avatar_billing_result(wd, job)},
                "error": "数字人步骤未完成或生成范围需重新确认；已停止新提交，保留原片段及账单记录"}
    if resume_only and remaining_legacy_segments()["result"]["remaining_segments"]:
        return remaining_legacy_segments()
    await _cli(wd, "pipeline.py", "finish", "--workdir", wd)
    path = wd / "avatar/final_avatar.mp4"
    if not path.is_file():
        raise RuntimeError("数字人流程未产出真实视频")
    return {"status": "succeeded", "result": {"path": _relative(root, path), "scope": "project", "review_stage": "postprocess" if _postproduction(project)["trim_pauses"] else "video",
        "billing": _avatar_billing_result(wd, job),
        "qa_path": _relative(root, wd / "avatar/qa.json") if (wd / "avatar/qa.json").exists() else None}}


def recover_receipt(job: dict, project: dict, workdir: Path) -> str | None:
    """Read only local provider receipts. No network, CLI, key reads or state writes."""
    if job.get("kind") == "scene_batch" and job.get("revision") == project.get("revision"):
        try:
            ledger = Path(workdir).resolve() / "scenes" / f"{_identifier(job['id'])}-ledger.json"
            state = json.loads(ledger.read_text(encoding="utf-8"))
            if state.get("scene_ids") == _scene_batch_ids(job, project) and state.get("scene_results"):
                return f"batch-ledger:{_identifier(job['id'])}"
        except (OSError, ValueError, TypeError, HTTPException):
            pass
        return None
    if job.get("kind") != "avatar" or job.get("revision") != project.get("revision"):
        return None
    wd = _skill_workdir(Path(workdir).resolve(), project)
    ids = {}
    try:
        state_path = wd / "avatar/result.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        for section in ("pending", "results"):
            for key, value in state.get(section, {}).items():
                if str(key).isdigit() and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(value.get("task", ""))):
                    ids[str(key)] = value["task"]
        for path in (wd / "avatar").glob("submission_*.json"):
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if str(receipt.get("segment", "")).isdigit() and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(receipt.get("task", ""))):
                ids[str(receipt["segment"])] = receipt["task"]
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if not ids:
        return None
    value = json.dumps(ids, sort_keys=True)
    return value if len(value) < 480 else f"oldx-ledger:revision-{project['revision']}"


def _job_result(project, kind, scene_id=None):
    if kind == "scene":
        candidates = []
        for item in project.get("jobs", []):
            if item.get("revision") != project.get("revision"):
                continue
            if item.get("kind") == "scene" and item.get("status") == "succeeded" and str(item.get("scene_id")) == str(scene_id):
                candidates.append(item.get("result", {}))
            elif item.get("kind") == "scene_batch":
                candidates.extend(row for row in (item.get("result") or {}).get("scene_results", []) if row.get("status") == "succeeded" and str(row.get("scene_id")) == str(scene_id))
        if candidates:
            return candidates[-1]
    jobs = [j for j in project.get("jobs", []) if j.get("kind") == kind and j.get("status") == "succeeded"
            and j.get("revision") == project.get("revision") and (scene_id is None or str(j.get("scene_id")) == str(scene_id))]
    if not jobs:
        raise HTTPException(409, f"缺少当前版本的 {kind} 媒体，请先完成生成")
    return jobs[-1].get("result", {})


def _postproduction(project):
    options = project.get("plan", {}).get("postproduction", {})
    return {"trim_pauses": options.get("trim_pauses") is True,
            "burn_subtitles": options.get("burn_subtitles", project.get("mode") != "avatar") is True}


def _postprocess_review(project):
    record = _review(project, "postprocess")
    avatars = [job for job in project.get("jobs", []) if job.get("kind") == "avatar" and job.get("status") == "succeeded" and job.get("revision") == project.get("revision")]
    if not avatars or record.get("job_id") != avatars[-1].get("id"):
        raise HTTPException(409, "去停顿确认必须对应当前数字人原片，重做原片后请重新确认")
    return record


async def _ffrun(*args):
    process = await asyncio.create_subprocess_exec(_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *map(str, args),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError("本地视频合成失败，请检查媒体格式与 ffmpeg 依赖")


def _stamp(seconds):
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


def _aligned_lengths(scenes, total_seconds, words):
    clean = lambda value: re.sub(r"[\W_]", "", str(value), flags=re.UNICODE).lower()
    expected = [clean(scene.get("narration", "")) for scene in scenes]
    observed = [clean(word.get("text", word.get("word", ""))) for word in words]
    if words and "".join(expected) == "".join(observed) and all(expected):
        boundaries, target, consumed, index = [0.0], 0, 0, 0
        for text in expected[:-1]:
            target += len(text)
            while index < len(words) - 1 and consumed + len(observed[index]) < target:
                consumed += len(observed[index])
                index += 1
            word = words[index]
            fraction = (target - consumed) / max(len(observed[index]), 1)
            boundaries.append(float(word["start"]) + (float(word["end"]) - float(word["start"])) * fraction)
        boundaries.append(total_seconds)
        lengths = [b - a for a, b in zip(boundaries, boundaries[1:])]
        if all(length > 0 for length in lengths):
            return lengths, "逐词时间对应每镜头旁白；词内按字数插值"
    planned = sum(float(scene["duration"]) for scene in scenes)
    if planned <= 0:
        raise HTTPException(409, "镜头时长不能为空")
    return [float(scene["duration"]) / planned * total_seconds for scene in scenes], "未取得完全匹配的逐词轴，按方案时长比例分配"


def _remap_words(words, mapping):
    """Derive export cues without mutating OLDX's original alignment files."""
    result = []
    for word in words:
        start, end = float(word["start"]), float(word["end"])
        overlaps = [(max(start, float(row["src_start"])), min(end, float(row["src_end"])), row) for row in mapping]
        overlaps = [row for row in overlaps if row[1] > row[0]]
        if overlaps:
            left, _, first = overlaps[0]
            _, right, last = overlaps[-1]
            result.append({**word, "start": float(first["out_start"]) + left - float(first["src_start"]),
                           "end": float(last["out_start"]) + right - float(last["src_start"])})
    return result


async def _render_job(job, project, root, callback):
    # The account-scoped Windows root is already long. Hash the complete valid
    # ID so platform metadata and atomic .tmp writes stay below MAX_PATH.
    # Existing exports keep their recorded paths and are never moved/deleted.
    render_id = hashlib.sha256(_identifier(job["id"]).encode("utf-8")).hexdigest()[:24]
    destination = root / "renders" / render_id
    destination.mkdir(parents=True, exist_ok=True)
    voice = _job_result(project, "voice")
    audio = _safe_path(root, voice["audio_path"])
    options = _postproduction(project)
    jumpcut = None
    skill_subtitle = None
    if options["trim_pauses"] and project.get("mode") != "avatar":
        raise HTTPException(422, "普通视频暂不支持去停顿，请关闭此选项后合成")
    if project.get("mode") == "avatar":
        avatar_result = _job_result(project, "avatar")
        avatar_source = _safe_path(root, avatar_result["path"])
        wd = _skill_workdir(root, project)
        if options["trim_pauses"]:
            _postprocess_review(project)
            if avatar_source != (wd / "avatar/final_avatar.mp4").resolve():
                raise HTTPException(409, "去停顿只能使用当前数字人项目的已确认原片")
            await _map_review(project, wd, "postprocess", "video")
            trimmed = await _cli(wd, "avatar/jumpcut.py", "--workdir", wd, "--yes")
            avatar_source = _safe_path(root, _relative(root, trimmed.get("out") or wd / "avatar/final_jumpcut.mp4"))
            jumpcut = json.loads((wd / "avatar/jumpcut.json").read_text(encoding="utf-8"))
            if not jumpcut.get("map") or any(check.get("ok") is not True for check in jumpcut.get("checks", [])):
                raise HTTPException(409, "去停顿后的音画同步自检未通过")
        final_words = wd / "avatar/words_final.json"
        if not final_words.exists():
            final_words = avatar_source.parent / "words_final.json"
        voice = {**voice, "words_path": _relative(root, final_words) if final_words.exists() else None}
        if options["burn_subtitles"]:
            if not final_words.is_file() or not (wd / "copy.txt").is_file():
                raise HTTPException(409, "烧录数字人字幕需要当前原文案与成片逐词时间，请先完成数字人拼接")
            caption_args = ["--video", avatar_source, "--copy", wd / "copy.txt", "--words", final_words,
                            "--out", destination / "avatar-captioned.mp4", "--yes"]
            if jumpcut:
                caption_args += ["--jumpcut", wd / "avatar/jumpcut.json"]
            captions = await _cli(wd, "avatar/captions.py", *caption_args)
            avatar_source = _safe_path(root, _relative(root, captions.get("out") or destination / "avatar-captioned.mp4"))
            skill_subtitle = _safe_path(root, _relative(root, captions.get("srt") or destination / "avatar-captioned.srt"))
        # OLDX finalize may trim heads. Keep its original synchronized soundtrack,
        # instead of laying the untrimmed TTS over the shorter avatar timeline.
        audio = destination / "avatar-audio.wav"
        await _ffrun("-i", avatar_source, "-vn", "-c:a", "pcm_s16le", audio)
    with wave.open(str(audio), "rb") as stream:
        total_seconds = stream.getnframes() / stream.getframerate()
    scenes = project["plan"]["scenes"]
    words = json.loads(_safe_path(root, voice["words_path"]).read_text(encoding="utf-8")).get("words", []) if voice.get("words_path") else []
    if jumpcut:
        words = _remap_words(words, jumpcut["map"])
    scaled, timing_basis = _aligned_lengths(scenes, total_seconds, words)
    parts = []
    if project.get("mode") == "avatar":
        source = avatar_source
        prepared = destination / "picture.mp4"
        await _ffrun("-i", source, "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25", "-an", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", prepared)
    else:
        for index, (scene, duration) in enumerate(zip(scenes, scaled)):
            source = _safe_path(root, _job_result(project, "scene", scene["id"])["path"])
            part = destination / f"part-{index:03}.mp4"
            # Looping maintains full narration; the export manifest discloses this treatment.
            await _ffrun("-stream_loop", "-1", "-i", source, "-t", f"{duration:.4f}", "-vf",
                "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25",
                "-an", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", part)
            parts.append(part)
            await _notify(callback, status="running", progress=(index + 1) / (len(scenes) + 2))
        listing = destination / "concat.txt"
        listing.write_text("\n".join(f"file '{p.name}'" for p in parts), encoding="utf-8")
        prepared = destination / "picture.mp4"
        await _ffrun("-f", "concat", "-safe", "1", "-i", listing, "-c", "copy", prepared)
    subtitle = destination / "subtitles.srt"
    cursor, entries = 0.0, []
    for i, (scene, duration) in enumerate(zip(scenes, scaled)):
        # Sidecar cues are scene-aligned. ASR words are used when available for finer cues.
        entries.append(f"{i+1}\n{_stamp(cursor)} --> {_stamp(cursor + duration)}\n{scene.get('narration', '')}\n")
        cursor += duration
    if voice.get("words_path"):
        if words:
            entries, batch = [], []
            for word in words:
                batch.append(word)
                text = "".join(str(w.get("text", w.get("word", ""))) for w in batch)
                if len(text) >= 20 or re.search(r"[。！？!?]$", text):
                    entries.append(f"{len(entries)+1}\n{_stamp(float(batch[0]['start']))} --> {_stamp(float(batch[-1]['end']))}\n{text}\n")
                    batch = []
            if batch:
                text = "".join(str(w.get("text", w.get("word", ""))) for w in batch)
                entries.append(f"{len(entries)+1}\n{_stamp(float(batch[0]['start']))} --> {_stamp(float(batch[-1]['end']))}\n{text}\n")
    subtitle.write_text("\n".join(entries), encoding="utf-8")
    if skill_subtitle:
        shutil.copy2(skill_subtitle, subtitle)
    final = destination / "master.mp4"
    audio_plan = project["plan"].get("audio_plan", {})
    command = ["-i", prepared, "-i", audio]
    music = audio_plan.get("music_path")
    if music:
        command += ["-stream_loop", "-1", "-i", _safe_path(root, music), "-filter_complex", "[1:a]volume=1[n];[2:a]volume=0.10[m];[n][m]amix=inputs=2:duration=first:normalize=0[a]", "-map", "0:v", "-map", "[a]"]
    else:
        command += ["-map", "0:v", "-map", "1:a"]
    # SRT is always included. Burning requires libass and uses a fixed safe relative filename.
    burn = options["burn_subtitles"]
    if burn and not skill_subtitle:
        escaped = str(subtitle.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        command += ["-vf", f"subtitles=filename='{escaped}':force_style='FontSize=18,MarginV=160,Alignment=2'", "-c:v", "libx264", "-preset", "fast"]
    else:
        command += ["-c:v", "copy"]
    command += ["-t", f"{total_seconds:.4f}", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", final]
    await _ffrun(*command)
    # Decode actual output before marking any export successful.
    await _ffrun("-i", final, "-f", "null", "-")
    cover = destination / "cover.jpg"
    await _ffrun("-i", final, "-frames:v", "1", cover)
    exports = []
    platforms = project.get("platforms") or list(PLATFORMS)
    for platform in platforms:
        key = {"视频号": "weixin", "抖音": "douyin", "小红书": "xiaohongshu", "wechat": "weixin"}.get(platform, platform)
        if key not in PLATFORMS:
            raise HTTPException(422, "不支持的平台")
        folder = destination / key
        folder.mkdir(exist_ok=True)
        for name, source in (("video.mp4", final), ("subtitles.srt", subtitle), ("cover.jpg", cover)):
            shutil.copy2(source, folder / name)
        variant = project["plan"].get("platform_variants", {}).get(key, {})
        title = variant.get("title") or project["plan"].get("brief", {}).get("title", project.get("topic", ""))
        manifest = {"platform": key, "platform_name": PLATFORMS[key], "title": title,
            "caption": variant.get("caption", ""), "cover_direction": variant.get("cover_direction", ""), "layout_notes": variant.get("layout_notes", ""),
            "duration": round(total_seconds, 3), "ratio": "9:16", "resolution": "720x1280", "same_edit_as_master": True,
            "same_cover_as_master": True, "cover_treatment": "成片第一帧；封面方向仅作为发布制作说明，未自动绘制专属封面",
            "ai_generated": True, "needs_platform_ai_label": True, "status": "awaiting_video_review",
            "layout": "竖屏统一版；未自动制作不同平台节奏与布局", "subtitles_burned": burn,
            "postproduction": {**options, "source_duration": jumpcut.get("source_seconds") if jumpcut else round(total_seconds, 3),
                               "removed_seconds": jumpcut.get("removed_seconds", 0) if jumpcut else 0, "cuts": jumpcut.get("cuts", 0) if jumpcut else 0},
            "timing_basis": timing_basis,
            "audio": "旁白；有用户提供的音乐时以10%音量混音", "scene_treatment": "按配音时长分配画面，必要时循环镜头"}
        _dump(folder / "manifest.json", manifest)
        (folder / "publication.md").write_text(f"# {PLATFORMS[key]}发布说明\n\n标题：{title}\n\n发布文案：{variant.get('caption', '')}\n\n封面方向：{variant.get('cover_direction', '')}\n\n布局建议：{variant.get('layout_notes', '')}\n\n实际导出：三个平台复用同一竖屏剪辑和首帧封面；未单独重排画面。\n", encoding="utf-8")
        exports.append({"platform": key, "path": _relative(root, folder / "video.mp4"), "manifest_path": _relative(root, folder / "manifest.json"),
                        "cover_path": _relative(root, folder / "cover.jpg"), "subtitle_path": _relative(root, folder / "subtitles.srt"),
                        "publication_path": _relative(root, folder / "publication.md"), "title": title, "caption": variant.get("caption", ""), "same_edit_as_master": True})
    return {"status": "succeeded", "result": {"path": _relative(root, final), "exports": exports, "duration": round(total_seconds, 3), "review_stage": "video", "postproduction": options,
        "jumpcut": {key: jumpcut.get(key) for key in ("source_seconds", "out_seconds", "removed_seconds", "cuts", "checks")} if jumpcut else None}}


async def execute_job(job: dict, project: dict, workdir: Path, progress_callback=None) -> dict:
    validate_job(job, project)
    root = Path(workdir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    handler = {"scene": _scene_job, "scene_batch": _scene_batch_job, "voice": _voice_job, "avatar": _avatar_job, "render": _render_job}[job["kind"]]
    try:
        return await handler(job, project, root, progress_callback)
    except HTTPException:
        raise
    except Exception:
        # Never return raw provider exceptions (may include signed URLs or secrets).
        return {"status": "blocked", "error": "制作步骤未完成，保留原任务和媒体；请核对运行环境或供应商任务后继续"}
