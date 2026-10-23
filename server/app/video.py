import json
import hashlib
import math
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import require_api_key
from .config import get_settings


router = APIRouter(prefix="/api/v1/video", dependencies=[Depends(require_api_key)])
SEEDANCE_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
SEEDANCE_MODEL = "doubao-seedance-2-0-mini-260615"
SEEDANCE_ROUTE = "/contents/generations/tasks"


class PromptComposeRequest(BaseModel):
    target: Literal[
        "plan",
        "brief",
        "brief.title",
        "brief.audience",
        "brief.core_message",
        "audio_plan",
        "audio_plan.narration_style",
        "audio_plan.voice",
        "audio_plan.music",
        "audio_plan.sound_notes",
        "scene",
        "scene.narration",
        "scene.visual_intent",
        "scene.seedance_prompt",
    ]
    instruction: str = Field(default="", max_length=1000)
    context: dict[str, Any]
    locked_fields: list[str] = Field(default_factory=list)


class ReferenceImage(BaseModel):
    name: str = Field(max_length=255)
    data_url: str = Field(min_length=1)
    role: Literal["first_frame", "last_frame", "reference_image"] = "reference_image"


class GenerationCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=10000)
    duration: int = Field(default=6, ge=4, le=15)
    resolution: Literal["480p", "720p"] = "720p"
    ratio: Literal["9:16"] = "9:16"
    references: list[ReferenceImage] = Field(default_factory=list, max_length=9)


def _skill_instructions() -> str:
    path = (
        Path(__file__).resolve().parents[2]
        / "hermes-skill"
        / "video-prompt-director"
        / "SKILL.md"
    )
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "Return strict JSON for an editable short-video plan and do not modify locked fields."


def _json_content(raw: str) -> Any:
    value = raw.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        value = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="提示词模型没有返回有效 JSON") from exc


async def _post_json(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="无法连接 AI 服务") from exc
    if response.is_error:
        try:
            detail = response.json().get("error", {}).get("message") or response.json().get("message")
        except (ValueError, AttributeError):
            detail = None
        raise HTTPException(status_code=502, detail=detail or f"AI 服务返回 HTTP {response.status_code}")
    return response.json()


async def _get_json(url: str, api_key: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="无法查询 Seedance 任务") from exc
    if response.is_error:
        raise HTTPException(status_code=502, detail=f"Seedance 查询返回 HTTP {response.status_code}")
    return response.json()


@router.post("/prompts/compose")
async def compose_prompt(payload: PromptComposeRequest) -> dict[str, Any]:
    settings = get_settings()
    api_key = settings.prompt_api_key or settings.seedance_api_key
    if not api_key or not settings.prompt_model:
        raise HTTPException(
            status_code=503,
            detail="提示词 AI 未配置，请设置 TASK_PROMPT_MODEL 和 TASK_PROMPT_API_KEY",
        )
    user_prompt = {
        "target": payload.target,
        "instruction": payload.instruction,
        "locked_fields": payload.locked_fields,
        "current_context": payload.context,
    }
    result = await _post_json(
        f"{settings.prompt_base_url.rstrip('/')}/chat/completions",
        api_key,
        {
            "model": settings.prompt_model,
            "temperature": 0.55,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _skill_instructions()},
                {
                    "role": "user",
                    "content": json.dumps(user_prompt, ensure_ascii=False),
                },
            ],
        },
    )
    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="提示词模型响应结构不完整") from exc
    return {"value": _json_content(content)}


@router.post("/generations", status_code=201)
async def create_generation(payload: GenerationCreate) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="请通过视频项目提交任务；需要当前版本确认和本批次预算授权")


def _generation_body(payload: GenerationCreate, model: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": payload.prompt}]
    content.extend(
        {
            "type": "image_url",
            "image_url": {"url": reference.data_url},
            "role": reference.role,
        }
        for reference in payload.references
    )
    return {"model": model,
            "content": content,
            "duration": payload.duration,
            "resolution": payload.resolution,
            "ratio": payload.ratio,
            "generate_audio": False,
            "watermark": False,
        }


def _build_generation_spec(payload: GenerationCreate, settings) -> dict[str, Any]:
    """Pin a new official Mini request without reading credentials or calling HTTP."""
    if (getattr(settings, "seedance_base_url", "").rstrip("/") != SEEDANCE_BASE_URL
            or getattr(settings, "seedance_model", "") != SEEDANCE_MODEL):
        raise HTTPException(503, "普通视频需要火山官方北京 Seedance 2.0 Mini 配置，不能自动切换通道")
    spec = {"provider": "seedance", "model": SEEDANCE_MODEL, "base_url": SEEDANCE_BASE_URL,
            "route": SEEDANCE_ROUTE, "body": _generation_body(payload, SEEDANCE_MODEL)}
    spec["request_fingerprint"] = hashlib.sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return spec


def _checked_generation_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Reject edited endpoints, models or request bodies before a new paid call."""
    try:
        if (not isinstance(spec, dict) or set(spec) != {"provider", "model", "base_url", "route", "body", "request_fingerprint"}
                or (spec["provider"], spec["model"], spec["base_url"], spec["route"]) != ("seedance", SEEDANCE_MODEL, SEEDANCE_BASE_URL, SEEDANCE_ROUTE)):
            raise ValueError()
        body = spec["body"]
        content = body["content"]
        if not isinstance(content, list) or not content or content[0].get("type") != "text":
            raise ValueError()
        references = [ReferenceImage(name="", data_url=item["image_url"]["url"], role=item["role"]) for item in content[1:]]
        payload = GenerationCreate(prompt=content[0]["text"], duration=body["duration"], resolution=body["resolution"],
            ratio=body["ratio"], references=references)
        if type(body["duration"]) is not int or any(type(body[key]) is not bool for key in ("generate_audio", "watermark")):
            raise ValueError()
        expected = _build_generation_spec(payload, SimpleNamespace(seedance_base_url=SEEDANCE_BASE_URL, seedance_model=SEEDANCE_MODEL))
        if spec != expected:
            raise ValueError()
        return expected
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(409, "Seedance 固定生成参数已变化，请重新确认生成") from None


async def _submit_generation(payload: GenerationCreate, *, execution_spec: dict | None = None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.seedance_api_key:
        raise HTTPException(status_code=503, detail="Seedance 未配置，请设置 ARK_API_KEY")
    spec = _checked_generation_spec(execution_spec) if execution_spec is not None else None
    base_url = spec["base_url"] if spec else settings.seedance_base_url.rstrip("/")
    result = await _post_json(f"{base_url}{SEEDANCE_ROUTE}", settings.seedance_api_key,
                             spec["body"] if spec else _generation_body(payload, settings.seedance_model))
    task_id = result.get("id")
    if not task_id:
        raise HTTPException(status_code=502, detail="Seedance 没有返回任务 ID")
    return {"id": task_id, "status": "queued"}


@router.get("/generations/{task_id}")
async def get_generation(task_id: str) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="请通过所属视频项目查询任务")


async def _query_generation(task_id: str, *, execution_spec: dict | None = None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.seedance_api_key:
        raise HTTPException(status_code=503, detail="Seedance 未配置，请设置 ARK_API_KEY")
    spec = _checked_generation_spec(execution_spec) if execution_spec is not None else None
    result = await _get_json(
        f"{spec['base_url'] if spec else settings.seedance_base_url.rstrip('/')}{SEEDANCE_ROUTE}/{task_id}",
        settings.seedance_api_key,
    )
    status_value = result.get("status", "failed")
    status_map = {"cancelled": "failed", "canceled": "failed"}
    normalized = status_map.get(status_value, status_value)
    content = result.get("content") or {}
    error = result.get("error")
    if isinstance(error, dict):
        error = error.get("message") or error.get("code")
    raw_usage = result.get("usage")
    usage = {
        name: raw_usage[name]
        for name in ("completion_tokens", "total_tokens")
        if isinstance(raw_usage, dict)
        and type(raw_usage.get(name)) is int
        and 0 <= raw_usage[name] <= 10**12
    }

    def numeric(name: str, maximum: int) -> int | float | None:
        value = result.get(name)
        if type(value) in (int, float) and 0 <= value <= maximum and math.isfinite(value):
            return value
        return None

    model = result.get("model")
    return {
        "id": result.get("id", task_id),
        "status": normalized,
        "videoUrl": content.get("video_url"),
        "error": str(error) if error else None,
        "usage": usage,
        "duration": numeric("duration", 3600),
        "fps": numeric("framespersecond", 240),
        "resolution": result.get("resolution") if result.get("resolution") in ("480p", "720p", "1080p", "4k") else None,
        "ratio": result.get("ratio") if result.get("ratio") in ("16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive") else None,
        "model": model if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model) else None,
    }
