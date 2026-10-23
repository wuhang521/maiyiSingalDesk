"""Owner-scoped, revisioned production API with explicit spend authorization.

Provider calls live in video_runtime. This module persists intent before starting
work, so a lost submit receipt is visible and never silently charged a second time.
"""
from __future__ import annotations

import hashlib
import base64
import binascii
import importlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Literal
from uuid import uuid4
import zipfile

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import AuthPrincipal, require_api_key
from .config import get_settings
from .database import get_db
from .models import utcnow
from .video import PromptComposeRequest, compose_prompt
from .video_project_models import VideoProject, VideoProjectJob, VideoProjectReview


router = APIRouter(prefix="/api/v1/video/projects", tags=["video-projects"])
Stage = Literal["inputs", "voice", "generation", "postprocess", "video"]
Kind = Literal["voice", "scene", "scene_batch", "avatar", "render"]
ACTIVE = {"queued", "running", "submitting"}
PROCESS_ID = str(uuid4())


class ProjectCreate(BaseModel):
    topic: str = Field(min_length=1, max_length=10000)
    mode: Literal["standard", "avatar"] = "standard"
    platforms: list[str] = Field(default_factory=lambda: ["视频号", "抖音", "小红书"], max_length=10)

    @model_validator(mode="after")
    def nonblank(self):
        self.topic = self.topic.strip()
        if not self.topic:
            raise ValueError("请输入视频主题")
        return self


class RevisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class PlanUpdate(RevisionRequest):
    plan: dict[str, Any]
    locked_fields: list[str] = Field(default_factory=list, max_length=200)


class ComposeRequest(RevisionRequest):
    instruction: str = Field(default="", max_length=2000)


class ReviewCreate(RevisionRequest):
    stage: Stage
    decision: Literal["approved", "skipped"]
    quote: str = Field(min_length=1, max_length=2000)
    job_id: str | None = Field(default=None, max_length=100)


class BudgetGrant(BaseModel):
    max_cost: float = Field(ge=0, allow_inf_nan=False)
    currency: Literal["CNY", "RH", "USD"]
    quote: str = Field(min_length=1, max_length=2000)


class JobCreate(RevisionRequest):
    kind: Kind
    scene_id: str | None = Field(default=None, max_length=100)
    scene_ids: list[str] | None = Field(default=None, min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=160)
    budget_grant: BudgetGrant | None = None
    quote_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    generation_consent: bool = False
    expected_provider: Literal["runninghub", "seedance"] | None = None


class AssetUpload(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    data_url: str = Field(min_length=1, max_length=14_000_000)
    role: Literal["avatar_image"] = "avatar_image"


class Scene(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=500)
    duration: int = Field(ge=1, le=15)
    narration: str = Field(min_length=1, max_length=10000)
    visual_intent: str = Field(min_length=1, max_length=10000)
    seedance_prompt: str = Field(min_length=1, max_length=20000)


class Plan(BaseModel):
    model_config = ConfigDict(extra="allow")
    brief: dict[str, Any]
    audio_plan: dict[str, Any]
    scenes: list[Scene] = Field(min_length=1, max_length=200)
    interest_hooks: list[dict[str, Any]] = Field(min_length=1, max_length=200)


def runtime_module():
    return importlib.import_module(".video_runtime", __package__)


def _cost_review_required() -> bool:
    return os.environ.get("TASK_VIDEO_COST_REVIEW_REQUIRED", "1").strip().lower() not in {"0", "false", "no"}


def _capabilities() -> dict[str, Any]:
    try:
        caps = runtime_module().runtime_capabilities()
    except (ImportError, AttributeError):
        caps = {key: {"ready": False, "reason": "视频运行时尚未安装"} for key in ("voice", "scene", "avatar", "render")}
    settings = get_settings()
    caps["planning"] = {
        **(caps.get("planning") or {}),
        "ready": bool(settings.prompt_model and (settings.prompt_api_key or settings.seedance_api_key)),
        "reason": "" if settings.prompt_model and (settings.prompt_api_key or settings.seedance_api_key) else "提示词模型尚未配置",
        "source": settings.video_planner_source,
        "model": settings.prompt_model,
    }
    caps["cost_review_required"] = _cost_review_required()
    return caps


def _owner(principal: AuthPrincipal) -> int:
    if principal.user is None:
        raise HTTPException(401, "视频项目需要个人账号登录，不能使用共享服务密钥")
    return principal.user.id


def _project(db: Session, project_id: str, owner_id: int) -> VideoProject:
    value = db.scalar(select(VideoProject).where(VideoProject.id == project_id, VideoProject.owner_id == owner_id))
    if value is None:
        raise HTTPException(404, "视频项目不存在")
    return value


def _revision(project: VideoProject, expected: int) -> None:
    if project.revision != expected:
        raise HTTPException(409, {"message": "方案已更新，请重新加载并确认", "revision": project.revision})


PLATFORM_ALIASES = {"weixin": "weixin", "视频号": "weixin", "微信视频号": "weixin",
                    "douyin": "douyin", "抖音": "douyin", "xiaohongshu": "xiaohongshu", "小红书": "xiaohongshu"}
PLATFORM_LAYOUTS = {"weixin": "9:16 竖屏，标题简洁，字幕避开底部和右侧操作区。",
                    "douyin": "9:16 竖屏，开头问题清楚可见，字幕避开底部和右侧操作区。",
                    "xiaohongshu": "9:16 竖屏，封面突出本片结论，字幕与重点图示分区。"}


def _canonical_plan_fields(parsed: dict[str, Any], mode: str) -> None:
    """Normalize legacy planning shapes once, before persistence and review."""
    parsed["mode"] = mode
    for key, default in (("persona", "知识讲解员，以通俗语言解释，不暗示专业执业资质。"),
                         ("visual_style", "清晰自然的科普画面，重点使用易懂的实景或示意。"),
                         ("layout", "9:16 竖屏，主体居中，重点文字与字幕分区，避开平台操作区。")):
        value = parsed.get(key)
        # The previous skill returned structured persona/style objects.
        if isinstance(value, dict):
            value = "；".join(str(item).strip() for item in value.values() if isinstance(item, (str, int, float)) and str(item).strip())
        if value is not None and not isinstance(value, str):
            raise HTTPException(422, f"{key} 应为可编辑的文字说明")
        parsed[key] = (value or "").strip() or default
    canonical = parsed.get("platform_variants") or {}
    legacy = parsed.pop("platform_versions", {}) or {}
    if not isinstance(canonical, dict):
        raise HTTPException(422, "platform_variants 应按 weixin/douyin/xiaohongshu 分组")
    if isinstance(legacy, list):
        legacy = {entry.get("platform", ""): entry for entry in legacy if isinstance(entry, dict)}
    if not isinstance(legacy, dict):
        raise HTTPException(422, "旧平台方案应为对象或包含 platform 的列表")
    merged = {}
    for source in (legacy, canonical):
        for name, entry in source.items():
            platform = PLATFORM_ALIASES.get(name)
            if platform:
                if not isinstance(entry, dict):
                    raise HTTPException(422, f"{name} 平台方案应为对象")
                merged[platform] = {**merged.get(platform, {}), **entry}
    result = {}
    title = str(parsed["brief"]["title"])
    for name in PLATFORM_LAYOUTS:
        entry = merged.get(name, {})
        defaults = {"title": title, "cover_direction": "用简洁封面突出“" + title + "”，不使用夸张承诺。",
                    "caption": str(parsed["brief"]["core_message"]), "layout_notes": PLATFORM_LAYOUTS[name]}
        result[name] = {}
        for key, default in defaults.items():
            value = entry.get(key)
            if value is not None and not isinstance(value, str):
                raise HTTPException(422, f"{name}.{key} 应为文字")
            result[name][key] = (value or "").strip() or default
    parsed["platform_variants"] = result
    options = parsed.get("postproduction", {})
    if not isinstance(options, dict) or any(key not in {"trim_pauses", "burn_subtitles"} for key in options):
        raise HTTPException(422, "后处理仅支持 trim_pauses 与 burn_subtitles 开关")
    defaults = {"trim_pauses": False, "burn_subtitles": mode != "avatar"}
    for key, default in defaults.items():
        if key in options and not isinstance(options[key], bool):
            raise HTTPException(422, f"后处理 {key} 应为 true 或 false")
    parsed["postproduction"] = {**defaults, **options}


def _validate_plan(value: dict[str, Any], mode: str) -> dict[str, Any]:
    try:
        parsed = Plan.model_validate(value).model_dump()
    except ValueError as exc:
        raise HTTPException(422, {"message": "方案不完整，需要定位、声音、分镜和兴趣钩子", "errors": str(exc)}) from exc
    for key in ("title", "audience", "core_message"):
        if not str(parsed["brief"].get(key, "")).strip():
            raise HTTPException(422, f"内容定位缺少 {key}")
    for key in ("narration_style", "voice", "music", "sound_notes"):
        if not str(parsed["audio_plan"].get(key, "")).strip():
            raise HTTPException(422, f"声音方案缺少 {key}")
    ids = [scene["id"] for scene in parsed["scenes"]]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "镜头 ID 不能重复")
    if mode == "standard" and any(scene["duration"] < 4 for scene in parsed["scenes"]):
        raise HTTPException(422, "普通视频单镜头需 4～15 秒，短钩子可放在镜头内部")
    if mode == "avatar" and any(scene["duration"] > 14 for scene in parsed["scenes"]):
        raise HTTPException(422, "数字人策划单段最长 14 秒；实际生成分段以配音检测结果为准")
    for hook in parsed["interest_hooks"]:
        hook["copy"] = hook.get("copy") or hook.get("text") or ""
        if hook.get("scene_id") not in ids or hook.get("payoff_scene_id") not in ids:
            raise HTTPException(422, "兴趣钩子必须关联提出问题和兑现答案的镜头")
        if ids.index(hook["payoff_scene_id"]) < ids.index(hook["scene_id"]):
            raise HTTPException(422, "兴趣钩子的答案应在提出问题的镜头或之后兑现")
        if not str(hook["copy"]).strip():
            raise HTTPException(422, "兴趣钩子缺少具体文案")
    parsed["brief"]["total_duration"] = sum(scene["duration"] for scene in parsed["scenes"])
    _canonical_plan_fields(parsed, mode)
    return parsed


def _reviews(db: Session, project: VideoProject) -> dict[str, Any]:
    rows = db.scalars(select(VideoProjectReview).where(VideoProjectReview.project_id == project.id,
        VideoProjectReview.revision == project.revision, VideoProjectReview.decision.in_(["approved", "skipped"]))).all()
    reviews = {}
    for row in rows:
        value = {"decision": row.decision, "revision": row.revision, "quote": row.quote,
                 "confirmed_at": row.confirmed_at.isoformat()}
        try:
            envelope = json.loads(row.quote)
            if isinstance(envelope, dict) and envelope.get("schema") == "video-review/v1":
                value.update({key: envelope[key] for key in ("quote", "job_id", "reused_from_revision") if key in envelope})
        except (ValueError, TypeError):
            pass
        reviews[row.stage] = value
    return reviews


def _job_dto(job: VideoProjectJob) -> dict[str, Any]:
    return {"id": job.id, "kind": job.kind, "scene_id": job.scene_id, "revision": job.revision,
            "scene_ids": (job.snapshot or {}).get("_scene_batch_ids") if job.kind == "scene_batch" else None,
            "status": job.status, "provider_task_id": job.provider_task_id, "result": job.result,
            "error": job.error, "estimated_cost": job.estimated_cost, "budget_grant": job.budget_grant,
            "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat()}


def _jobs(db: Session, project: VideoProject) -> list[dict[str, Any]]:
    return [_job_dto(job) for job in db.scalars(select(VideoProjectJob).where(VideoProjectJob.project_id == project.id).order_by(VideoProjectJob.created_at)).all()]


def _dto(db: Session, project: VideoProject) -> dict[str, Any]:
    return {"id": project.id, "topic": project.topic, "mode": project.mode, "platforms": project.platforms,
            "revision": project.revision, "status": project.status,
            "plan": _validate_plan(project.plan, project.mode) if project.plan else {},
            "locked_fields": project.locked_fields, "reviews": _reviews(db, project), "jobs": _jobs(db, project),
            "created_at": project.created_at.isoformat(), "updated_at": project.updated_at.isoformat(),
            "medical_gate": {"required": False, "policy": "user_content_confirmation"}, "capabilities": _capabilities()}


def _require_reviews(db: Session, project: VideoProject, stages: list[str]) -> None:
    reviews = _reviews(db, project)
    missing = [stage for stage in stages if stage not in reviews]
    if missing:
        raise HTTPException(428, {"message": "请先确认当前版本的中间结果", "missing_reviews": missing, "revision": project.revision})


def _scene(project: VideoProject, scene_id: str | None) -> dict[str, Any]:
    for scene in project.plan.get("scenes", []):
        if scene["id"] == scene_id:
            return scene
    raise HTTPException(422, "请选择有效镜头")


def _batch_scene_ids(project: VideoProject, db: Session, requested: list[str] | None = None) -> list[str]:
    ids = [scene["id"] for scene in project.plan.get("scenes", [])]
    completed = set()
    for job in _jobs(db, project):
        if job["revision"] != project.revision:
            continue
        if job["kind"] == "scene" and job["status"] == "succeeded" and job["result"].get("path"):
            completed.add(job["scene_id"])
        if job["kind"] == "scene_batch":
            completed.update(child.get("scene_id") for child in job["result"].get("scene_results", [])
                             if child.get("status") == "succeeded" and child.get("path"))
    selected = requested if requested is not None else [item for item in ids if item not in completed]
    if not selected or len(selected) != len(set(selected)) or any(item not in ids for item in selected):
        raise HTTPException(422, "请选择尚未完成且不重复的镜头；当前没有缺失镜头时可直接合成")
    if any(item in completed for item in selected):
        raise HTTPException(409, "批次只制作缺失镜头；重做已完成镜头需要单独询价与授权")
    return selected


def _quote(project: VideoProject, kind: str, scene_id: str | None, db: Session, scene_ids: list[str] | None = None) -> dict[str, Any]:
    if kind == "scene":
        _scene(project, scene_id)
    elif scene_id is not None:
        raise HTTPException(422, "配音、数字人和合成为整条项目任务，不接受单镜头范围")
    if kind == "scene_batch":
        if project.mode != "standard":
            raise HTTPException(422, "数字人按整条项目生成，不使用普通视频镜头批次")
        scene_ids = _batch_scene_ids(project, db, scene_ids)
    elif scene_ids is not None:
        raise HTTPException(422, "只有普通视频镜头批次接受 scene_ids")
    data = _dto(db, project)
    required = _cost_review_required()
    try:
        request = {"kind": kind, "scene_id": scene_id, "scene_ids": scene_ids}
        if required:
            quote = runtime_module().estimate_job(request, data)
        else:
            quote = {**runtime_module().prepare_job(request, data), "amount": None,
                     "basis": "不提供报价", "cost_review_required": False}
    except (ImportError, AttributeError):
        quote = {"amount": None, "currency": "CNY", "basis": "运行时或定价配置尚未安装",
                 "blocked_reason": "运行时或定价配置尚未安装"}
    except (ValueError, HTTPException) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        quote = {"amount": None, "currency": "CNY", "basis": str(detail), "blocked_reason": str(detail)}
    quote = dict(quote)
    if not required:
        quote.update(amount=None, basis="不提供报价", cost_review_required=False)
    quote.update({"revision": project.revision, "kind": kind, "scene_id": scene_id,
                  "scope": "scene" if kind == "scene" else "scene_batch" if kind == "scene_batch" else "project"})
    if kind == "scene_batch":
        quote["scene_ids"] = scene_ids
    # Only stable authorization facts enter the hash. Preview request IDs,
    # fetched-at timestamps and human wording can change without changing price.
    if required:
        binding = {key: quote.get(key) for key in ("project_id", "revision", "kind", "scene_id", "scene_ids",
                   "provider", "model", "route", "request_fingerprint", "amount", "reservation_amount", "currency")}
        binding["project_id"] = project.id
        quote["quote_fingerprint"] = hashlib.sha256(json.dumps(binding, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    if required and quote.get("amount") is None:
        quote["blocked_reason"] = quote.get("basis") or "缺少可解释的费用上界，暂停付费生成"
    else:
        capability = data["capabilities"].get(kind, {})
        if capability.get("ready") is False:
            quote["blocked_reason"] = capability.get("reason") or "运行时尚未配置"
    return quote


def _path_parts(value: Any, path: str):
    parts = path.split(".")
    for part in parts[:-1]:
        if isinstance(value, list):
            value = value[int(part)] if part.isdigit() else next(item for item in value if item.get("id") == part)
        else:
            value = value[part]
    return value, parts[-1]


def _locked(old: dict[str, Any], new: dict[str, Any], paths: list[str]) -> None:
    for path in paths:
        try:
            old_parent, key = _path_parts(old, path)
            new_parent, new_key = _path_parts(new, path)
            if isinstance(old_parent, list):
                key = int(key)
                new_key = int(new_key)
            new_parent[new_key] = old_parent[key]
        except (KeyError, IndexError, TypeError, ValueError, StopIteration) as exc:
            raise HTTPException(422, f"锁定字段路径无效：{path}") from exc


def _cleanup_reuse_staging(staging: Path, project_root: Path) -> None:
    """Remove only the uniquely named staging directory created by this call."""
    root, target = project_root.resolve(), staging.resolve()
    if staging.is_symlink() or (hasattr(staging, "is_junction") and staging.is_junction()):
        raise OSError("素材暂存目录不能是链接")
    if target.parent != root or not target.is_relative_to(root) or not re.fullmatch(r"\.r[a-z0-9_]{8}", staging.name):
        raise OSError("素材暂存目录不在本项目内")
    if staging.is_dir():
        shutil.rmtree(staging)


def _save_plan(db: Session, project: VideoProject, expected: int, plan: dict[str, Any], locks: list[str]) -> dict[str, Any]:
    _revision(project, expected)
    plan = _validate_plan(plan, project.mode)
    changed = plan != project.plan
    old_plan = _validate_plan(project.plan, project.mode) if project.plan else {}
    postproduction_only = changed and bool(old_plan) and (
        {key: value for key, value in plan.items() if key != "postproduction"}
        == {key: value for key, value in old_plan.items() if key != "postproduction"}
    ) and plan.get("postproduction") != old_plan.get("postproduction")
    reusable_jobs = []
    reusable_reviews = {}
    staging = None
    destination = None
    promoted = False
    if postproduction_only:
        active = db.scalar(select(VideoProjectJob).where(VideoProjectJob.project_id == project.id,
            VideoProjectJob.revision == expected, VideoProjectJob.status.in_(ACTIVE)).limit(1))
        if active:
            raise HTTPException(409, "制作中不能修改后处理选项，请等当前任务结束")
        reusable_jobs = db.scalars(select(VideoProjectJob).where(VideoProjectJob.project_id == project.id,
            VideoProjectJob.revision == expected, VideoProjectJob.status == "succeeded",
            VideoProjectJob.kind.in_(["voice", "scene", "scene_batch", "avatar"])).order_by(VideoProjectJob.created_at)).all()
        reusable_reviews = {stage: value for stage, value in _reviews(db, project).items() if stage in {"inputs", "voice", "generation"}}
        source, destination = _workdir(project, expected), _workdir(project, expected + 1)
        if destination.exists():
            raise HTTPException(409, "新版媒体目录已存在，请重新加载项目后重试")
    values = {"plan": plan, "locked_fields": locks, "updated_at": utcnow()}
    if changed:
        values.update(revision=expected + 1, status="planned")
    result = db.execute(update(VideoProject).where(VideoProject.id == project.id, VideoProject.revision == expected).values(**values))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "方案被其他操作更新，请重新加载")
    try:
        if postproduction_only:
            if reusable_jobs and not source.is_dir():
                raise OSError("原版本的本地素材目录不存在")
            if source.is_dir():
                # CAS holds the project writer lock before any copy. Staging is
                # unique, so a partial copy can be removed without touching an
                # existing revision or blocking the next attempt.
                # Keep the basename no longer than "revision-1". The real local
                # Windows storage tree plus a 64-character asset hash can already
                # approach MAX_PATH; staging must not add another 30 characters.
                staging = Path(tempfile.mkdtemp(prefix=".r", dir=source.parent))
                shutil.copytree(source, staging, symlinks=False, dirs_exist_ok=True, ignore=shutil.ignore_patterns("renders"))
                old_skill = staging / "skill" / f"revision-{expected}"
                if old_skill.is_dir():
                    old_skill.rename(staging / "skill" / f"revision-{expected + 1}")
            for old in reusable_jobs:
                snapshot = {**(old.snapshot or {}), "revision": expected + 1, "plan": plan}
                def relocate(value):
                    if isinstance(value, dict):
                        return {key: relocate(item) for key, item in value.items()}
                    if isinstance(value, list):
                        return [relocate(item) for item in value]
                    if isinstance(value, str):
                        prefix = f"skill/revision-{expected}/"
                        return f"skill/revision-{expected + 1}/" + value[len(prefix):] if value.startswith(prefix) else value
                    return value
                db.add(VideoProjectJob(id=str(uuid4()), project_id=project.id, revision=expected + 1, kind=old.kind,
                    scene_id=old.scene_id, idempotency_key="local-reuse-" + old.id, request_hash=old.request_hash,
                    status="succeeded", result={**relocate(old.result), "reused_from_job_id": old.id, "reused_from_revision": expected},
                    estimated_cost={"amount": 0, "currency": "CNY", "scope": "local_reuse", "basis": "仅后处理变更，复用本机已完成素材，无供应商调用"},
                    budget_grant={}, snapshot=snapshot))
            for stage, value in reusable_reviews.items():
                db.add(VideoProjectReview(id=str(uuid4()), project_id=project.id, revision=expected + 1, stage=stage,
                    decision=value["decision"], quote=json.dumps({"schema": "video-review/v1", "quote": value["quote"],
                    "reused_from_revision": expected}, ensure_ascii=False)))
            # Check database constraints before promoting the temporary copy.
            db.flush()
            if staging:
                if destination.exists():
                    raise HTTPException(409, "新版媒体目录已存在，请重新加载项目后重试")
                staging.rename(destination)
                promoted = True
        db.commit()
    except Exception as exc:
        db.rollback()
        if staging:
            # Only move back the directory promoted by this exact call. Never
            # remove a destination that was already present before the copy.
            if promoted and destination.is_dir() and not staging.exists():
                destination.rename(staging)
            _cleanup_reuse_staging(staging, source.parent)
        if isinstance(exc, OSError):
            raise HTTPException(503, "本地素材复用未完成；原版本保留，请检查磁盘空间后重试") from exc
        raise
    db.refresh(project)
    return _dto(db, project)


def _workdir(project: VideoProject, revision: int | None = None) -> Path:
    root = Path(getattr(get_settings(), "video_storage_dir", "./video-project-files")).expanduser().resolve()
    return root / str(project.owner_id) / project.id / f"revision-{revision or project.revision}"


async def _execute_job(job_id: str, bind: Any, *, resume: bool = False) -> None:
    """Claim once, then persist provider receipts independently of HTTP lifetime."""
    token = f"{PROCESS_ID}:{uuid4()}"
    with Session(bind=bind, expire_on_commit=False) as db:
        job = db.get(VideoProjectJob, job_id)
        if job is None or job.status == "succeeded":
            return
        if resume and not job.provider_task_id and job.kind != "render":
            job.status = "needs_attention"
            job.error = "提交回执缺失，已停止自动重发，请先核对供应商任务与账单"
            db.commit()
            return
        allowed = ["queued"] if not resume else ["queued", "running", "submitting", "needs_attention"]
        claimed = db.execute(update(VideoProjectJob).where(VideoProjectJob.id == job_id, VideoProjectJob.status.in_(allowed), VideoProjectJob.lease_token.is_(None)).values(status="submitting", lease_token=token, updated_at=utcnow()))
        db.commit()
        if claimed.rowcount != 1:
            return
        db.refresh(job)
        project = db.get(VideoProject, job.project_id)
        workdir = _workdir(project, job.revision)
        workdir.mkdir(parents=True, exist_ok=True)
        asset_dir = workdir.parent / "assets"
        if asset_dir.is_dir():
            for asset in asset_dir.iterdir():
                if asset.is_file() and asset.suffix.lower() in {".png", ".jpg", ".webp"}:
                    destination = workdir / "assets" / asset.name
                    destination.parent.mkdir(exist_ok=True)
                    shutil.copy2(asset, destination)
        runtime_job = _job_dto(job)
        # A new job was just marked submitting by us, not a recovered receipt loss.
        runtime_job["status"] = "queued" if not resume else ("running" if job.provider_task_id else "queued")
        snapshot = dict(job.snapshot)
        # The approved route survives configuration changes and process restarts.
        # Historical jobs without this field remain a legacy runtime decision.
        if isinstance(snapshot.get("_scene_execution"), dict):
            runtime_job["execution"] = snapshot["_scene_execution"]

    async def progress(values: dict[str, Any]) -> None:
        with Session(bind=bind) as session:
            current = session.get(VideoProjectJob, job_id)
            if current is None or current.lease_token != token:
                return
            for key in ("provider_task_id", "status", "result", "error"):
                if key in values:
                    setattr(current, key, values[key])
            current.updated_at = utcnow()
            session.commit()

    try:
        outcome = await runtime_module().execute_job(runtime_job, snapshot, workdir, progress)
        if not isinstance(outcome, dict):
            raise ValueError("运行时返回结果格式不正确")
    except Exception as exc:
        # Do not turn an ambiguous provider submit into an automatic retry.
        with Session(bind=bind) as session:
            current = session.get(VideoProjectJob, job_id)
            if current and current.lease_token == token:
                current.status = "needs_attention" if current.status == "submitting" and not current.provider_task_id else "failed"
                current.error = str(exc.detail if isinstance(exc, HTTPException) else exc)[:2000]
                current.lease_token = None
                current.updated_at = utcnow()
                session.commit()
        return
    with Session(bind=bind) as session:
        current = session.get(VideoProjectJob, job_id)
        if current is None or current.lease_token != token:
            return
        current.status = outcome.get("status", "failed")
        if current.status not in {"queued", "running", "succeeded", "blocked", "failed", "needs_attention"}:
            current.status = "failed"
        current.provider_task_id = outcome.get("provider_task_id") or current.provider_task_id
        current.result = outcome.get("result") or current.result or {}
        current.error = outcome.get("error")
        current.lease_token = None
        current.updated_at = utcnow()
        project = session.get(VideoProject, current.project_id)
        if project and project.revision == current.revision:
            if current.status == "succeeded" and current.kind in {"voice", "render"}:
                project.status = "awaiting_voice_review" if current.kind == "voice" else "awaiting_video_review"
            elif current.status in {"blocked", "failed", "needs_attention"}:
                project.status = "needs_attention"
        session.commit()


@router.get("/capabilities")
def capabilities(principal: AuthPrincipal = Depends(require_api_key)):
    _owner(principal)
    return _capabilities()


@router.get("")
def list_projects(db: Session = Depends(get_db), principal: AuthPrincipal = Depends(require_api_key)):
    owner = _owner(principal)
    projects = db.scalars(select(VideoProject).where(VideoProject.owner_id == owner).order_by(VideoProject.updated_at.desc()).limit(100)).all()
    return {"items": [_dto(db, project) for project in projects]}


@router.post("", status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db), principal: AuthPrincipal = Depends(require_api_key)):
    project = VideoProject(id=str(uuid4()), owner_id=_owner(principal), topic=payload.topic, mode=payload.mode,
                           platforms=payload.platforms, revision=1, status="draft", plan={}, locked_fields=[])
    db.add(project)
    db.commit()
    return _dto(db, project)


@router.get("/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db), principal: AuthPrincipal = Depends(require_api_key)):
    return _dto(db, _project(db, project_id, _owner(principal)))


@router.put("/{project_id}/plan")
def save_plan(project_id: str, payload: PlanUpdate, db: Session = Depends(get_db), principal: AuthPrincipal = Depends(require_api_key)):
    project = _project(db, project_id, _owner(principal))
    retained = [path for path in project.locked_fields if path in payload.locked_fields]
    _locked(project.plan, payload.plan, retained)
    return _save_plan(db, project, payload.expected_revision, payload.plan, payload.locked_fields)


@router.post("/{project_id}/compose")
async def compose(project_id: str, payload: ComposeRequest, db: Session = Depends(get_db), principal: AuthPrincipal = Depends(require_api_key)):
    project = _project(db, project_id, _owner(principal))
    _revision(project, payload.expected_revision)
    context = {"topic": project.topic, "mode": project.mode, "platforms": project.platforms,
               "current_plan": project.plan}
    if project.mode == "standard":
        capability = _capabilities().get("scene") or {}
        constraints = {key: capability[key] for key in ("provider", "model", "route", "constraints",
            "min_duration", "max_duration", "duration_min", "duration_max", "min_seconds", "max_seconds",
            "durations", "resolutions", "ratios") if key in capability}
        if constraints:
            context["scene_constraints"] = constraints
    response = await compose_prompt(PromptComposeRequest(target="plan", instruction=payload.instruction,
        context=context, locked_fields=project.locked_fields))
    plan = response.get("value")
    if not isinstance(plan, dict):
        raise HTTPException(502, "AI 没有返回完整方案对象")
    _locked(project.plan, plan, project.locked_fields)
    return _save_plan(db, project, payload.expected_revision, plan, project.locked_fields)


@router.post("/{project_id}/reviews")
def review(project_id: str, payload: ReviewCreate, db: Session = Depends(get_db), principal: AuthPrincipal = Depends(require_api_key)):
    project = _project(db, project_id, _owner(principal))
    _revision(project, payload.expected_revision)
    _validate_plan(project.plan, project.mode)
    if not payload.quote.strip():
        raise HTTPException(422, "请保留实际确认或跳过的原话")
    if payload.stage == "inputs" and project.mode == "avatar":
        avatar = project.plan.get("avatar") or {}
        if avatar.get("image_use_confirmed") is not True:
            raise HTTPException(428, "请先确认人物图的使用授权；跳过创作审核不能代替肖像授权")
        image = avatar.get("image_path", "")
        if not isinstance(image, str) or not image.startswith("assets/") or Path(image).suffix.lower() not in {".png", ".jpg", ".webp"}:
            raise HTTPException(428, "请先上传并确认本项目的人物图片")
        _media_path(project, image)
    jobs = _jobs(db, project)
    stored_quote = payload.quote.strip()
    if payload.stage == "postprocess":
        if project.mode != "avatar" or payload.decision != "approved":
            raise HTTPException(422, "数字人去停顿前需查看原片并明确确认，不能跳过")
        candidates = [item for item in jobs if item["kind"] == "avatar" and item["revision"] == project.revision]
        latest = candidates[-1] if candidates else None
        if not latest or latest["status"] != "succeeded" or not latest["result"].get("path") or payload.job_id != latest["id"]:
            raise HTTPException(428, "请播放并确认当前版本最新的数字人原片，确认必须关联该原片任务")
        if any(item["revision"] == project.revision and item["kind"] == "voice" and item["created_at"] > latest["created_at"] for item in jobs):
            raise HTTPException(428, "配音在原片之后已重做，请先重新生成数字人原片")
        _media_path(project, latest["result"]["path"])
        stored_quote = json.dumps({"schema": "video-review/v1", "quote": payload.quote.strip(), "job_id": payload.job_id}, ensure_ascii=False)
    if payload.decision == "approved" and payload.stage in {"voice", "video"}:
        kind = "voice" if payload.stage == "voice" else "render"
        candidates = [job for job in jobs if job["kind"] == kind and job["revision"] == project.revision]
        if not candidates or candidates[-1]["status"] != "succeeded" or not candidates[-1]["result"]:
            raise HTTPException(428, f"当前版本尚无可供确认的{ '配音' if kind == 'voice' else '成片' }")
        if kind == "render" and any(job["revision"] == project.revision and job["kind"] in {"voice", "scene", "scene_batch", "avatar"}
                                     and job["created_at"] > candidates[-1]["created_at"] for job in jobs):
            raise HTTPException(428, "素材在这版成片之后重新制作过，请先重新合成再验片")
    if payload.stage == "generation":
        _require_reviews(db, project, ["inputs", "voice"])
    row = db.scalar(select(VideoProjectReview).where(VideoProjectReview.project_id == project.id, VideoProjectReview.revision == project.revision, VideoProjectReview.stage == payload.stage))
    if row is None:
        row = VideoProjectReview(id=str(uuid4()), project_id=project.id, revision=project.revision, stage=payload.stage,
                                 decision=payload.decision, quote=stored_quote)
        db.add(row)
    else:
        row.decision, row.quote, row.confirmed_at = payload.decision, stored_quote, utcnow()
    if payload.stage == "video":
        project.status = "ready" if payload.decision == "approved" else "review_skipped"
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "确认记录已更新，请重新加载") from exc
    return _dto(db, project)


@router.get("/{project_id}/quote")
def quote(project_id: str, kind: Kind, scene_id: str | None = None, scene_ids: list[str] | None = Query(default=None), db: Session = Depends(get_db), principal: AuthPrincipal = Depends(require_api_key)):
    project = _project(db, project_id, _owner(principal))
    return _quote(project, kind, scene_id, db, scene_ids)


@router.post("/{project_id}/assets", status_code=201)
def upload_asset(project_id: str, payload: AssetUpload, db: Session = Depends(get_db), principal: AuthPrincipal = Depends(require_api_key)):
    project = _project(db, project_id, _owner(principal))
    header, separator, encoded = payload.data_url.partition(",")
    extensions = {"data:image/png;base64": ".png", "data:image/jpeg;base64": ".jpg", "data:image/webp;base64": ".webp"}
    extension = extensions.get(header)
    if not separator or extension is None:
        raise HTTPException(422, "人物图仅支持 PNG、JPEG 或 WebP")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "图片编码无效") from exc
    if not content or len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "人物图应小于 10 MB")
    signature_ok = ((extension == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24)
                    or (extension == ".jpg" and content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"))
                    or (extension == ".webp" and content.startswith(b"RIFF") and content[8:12] == b"WEBP"))
    if not signature_ok:
        raise HTTPException(422, "图片内容与声明的格式不符")
    name = hashlib.sha256(content).hexdigest() + extension
    directory = _workdir(project).parent / "assets"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(content)
    return {"path": "assets/" + name, "name": payload.name, "role": payload.role,
            "sha256": name.split(".")[0], "size": len(content), "validation": "file_signature", "requires_image_review": True}


def _check_active_jobs(project: VideoProject, payload: JobCreate, db: Session, *, locked: bool = False) -> None:
    statement = select(VideoProjectJob).where(VideoProjectJob.project_id == project.id,
        VideoProjectJob.revision == project.revision, VideoProjectJob.status.in_(ACTIVE))
    active = db.scalars(statement.with_for_update() if locked else statement).all()
    shared_writer = payload.kind in {"voice", "avatar", "scene_batch"} or any(row.kind in {"voice", "avatar", "scene_batch"} for row in active)
    duplicate_scene = any(row.kind == payload.kind and row.scene_id == payload.scene_id for row in active)
    if active and (shared_writer or duplicate_scene or payload.kind == "render"):
        db.rollback()
        raise HTTPException(409, "当前制作仍在进行，请继续原任务；同一素材不能重复提交")


def _check_job_inputs(project: VideoProject, payload: JobCreate, db: Session) -> None:
    """Read-only eligibility checks, repeated after the quote outside our transaction."""
    _validate_plan(project.plan, project.mode)
    if payload.kind != "scene" and payload.scene_id is not None:
        raise HTTPException(422, "配音、数字人和合成为整条项目任务，不接受单镜头范围")
    if payload.kind != "scene_batch" and payload.scene_ids is not None:
        raise HTTPException(422, "只有普通视频镜头批次接受 scene_ids")
    if payload.kind == "avatar" and project.mode != "avatar":
        raise HTTPException(422, "普通视频项目未选择数字人模式")
    if payload.kind == "scene":
        _scene(project, payload.scene_id)
    if payload.kind == "scene_batch":
        if project.mode != "standard":
            raise HTTPException(422, "数字人按整条项目生成，不使用普通视频镜头批次")
        if payload.scene_ids is None:
            raise HTTPException(422, "批次任务必须提交已询价确认的固定镜头名单 scene_ids")
        _batch_scene_ids(project, db, payload.scene_ids)
    required = ["inputs"] if payload.kind == "voice" else ["inputs", "voice", "generation"]
    _require_reviews(db, project, required)
    if payload.kind == "render" and project.mode == "avatar" and project.plan.get("postproduction", {}).get("trim_pauses"):
        _require_reviews(db, project, ["postprocess"])
        candidates = [item for item in _jobs(db, project) if item["kind"] == "avatar" and item["revision"] == project.revision]
        latest = candidates[-1] if candidates else None
        confirmation = _reviews(db, project).get("postprocess", {})
        if not latest or latest["status"] != "succeeded" or confirmation.get("job_id") != latest["id"]:
            raise HTTPException(428, "去停顿确认已过期，请重新查看并确认当前数字人原片")


def _job_snapshot(db: Session, project: VideoProject, payload: JobCreate, estimated: dict[str, Any]) -> dict[str, Any]:
    snapshot = _dto(db, project)
    if payload.kind == "scene_batch":
        snapshot["_scene_batch_ids"] = payload.scene_ids
    if payload.kind in {"scene", "scene_batch"} and isinstance(estimated.get("execution"), dict):
        snapshot["_scene_execution"] = estimated["execution"]
    return snapshot


def _job_request_hash(payload: JobCreate, *, direct: bool) -> str:
    request = {"kind": payload.kind, "scene_id": payload.scene_id,
               "scene_ids": payload.scene_ids, "revision": payload.expected_revision}
    if direct:
        request.update(generation_consent=payload.generation_consent, expected_provider=payload.expected_provider)
    elif payload.quote_fingerprint is not None:
        # Preserve the exact existing quote-mode idempotency representation.
        request["quote_fingerprint"] = payload.quote_fingerprint
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()


def _same_job_request(job: VideoProjectJob, payload: JobCreate) -> bool:
    direct = "generation_consent" in (job.budget_grant or {}) or (job.estimated_cost or {}).get("cost_review_required") is False
    if not direct and (payload.generation_consent or payload.expected_provider is not None):
        return False
    return job.request_hash == _job_request_hash(payload, direct=direct)


@router.post("/{project_id}/jobs", status_code=202)
def create_job(project_id: str, payload: JobCreate, background: BackgroundTasks, db: Session = Depends(get_db), principal: AuthPrincipal = Depends(require_api_key)):
    owner_id = _owner(principal)
    project = _project(db, project_id, owner_id)
    direct = not _cost_review_required()
    fingerprint = _job_request_hash(payload, direct=direct)
    existing = db.scalar(select(VideoProjectJob).where(VideoProjectJob.project_id == project.id, VideoProjectJob.idempotency_key == payload.idempotency_key))
    if existing:
        if not _same_job_request(existing, payload):
            raise HTTPException(409, "同一个幂等键不能用于不同任务")
        return _job_dto(existing)
    _revision(project, payload.expected_revision)
    _check_active_jobs(project, payload, db)
    _check_job_inputs(project, payload, db)
    # Price previews may perform network I/O. Neither this call nor runtime's
    # budget validation may hold a FOR UPDATE lock or SQLite writer transaction.
    grant: dict[str, Any] = {}
    if direct:
        if payload.kind != "render" and not payload.generation_consent:
            raise HTTPException(428, "请点击生成确认本次云端制作；费用由供应商按实际调用规则收取")
        try:
            prepared = runtime_module().prepare_job({"kind": payload.kind, "scene_id": payload.scene_id,
                "scene_ids": payload.scene_ids}, _dto(db, project))
        except (ImportError, AttributeError):
            raise HTTPException(503, "本机直接生成运行时尚未安装") from None
        if not isinstance(prepared, dict):
            raise HTTPException(503, "本机直接生成参数未准备完整")
        if payload.kind in {"scene", "scene_batch"}:
            execution = prepared.get("execution")
            if (payload.expected_provider not in {"seedance", "runninghub"} or prepared.get("provider") != payload.expected_provider
                    or not isinstance(execution, dict) or execution.get("provider") != payload.expected_provider):
                raise HTTPException(428, "视频生成通道已变化，请重新确认当前供应商后生成")
        estimated = {**prepared, "amount": None, "basis": "不提供报价", "cost_review_required": False}
        grant = {"approved": True, "revision": project.revision, "generation_consent": payload.generation_consent,
                 "scope": "scene" if payload.kind == "scene" else "scene_batch" if payload.kind == "scene_batch" else "project",
                 "kind": payload.kind, "scene_id": payload.scene_id, "scene_ids": payload.scene_ids,
                 "provider": prepared.get("provider"), "expected_provider": payload.expected_provider,
                 "intent": "用户点击生成，云端按供应商规则计费" if payload.kind != "render" else "用户请求本地合成，不调用付费供应商",
                 "authorized_at": utcnow().isoformat()}
    else:
        estimated = _quote(project, payload.kind, payload.scene_id, db, payload.scene_ids)
        if estimated.get("blocked_reason") or estimated.get("amount") is None:
            raise HTTPException(503, {"message": estimated.get("blocked_reason", "费用上界尚未配置"), "quote": estimated})
        if payload.kind in {"scene", "scene_batch"} and (estimated.get("provider") == "runninghub" or payload.quote_fingerprint is not None):
            if not payload.quote_fingerprint or payload.quote_fingerprint != estimated["quote_fingerprint"]:
                raise HTTPException(428, {"message": "视频通道、参数或报价已变化，请重新查看并确认当前费用", "quote": estimated})
            if estimated.get("provider") == "runninghub" and (not isinstance(estimated.get("execution"), dict) or not estimated["execution"]):
                raise HTTPException(503, "当前视频报价缺少固定执行参数，不能提交生成")
        if payload.kind != "render" or float(estimated["amount"]) > 0:
            supplied = payload.budget_grant
            if supplied is None or not supplied.quote.strip():
                raise HTTPException(428, "声音、样片和镜头均需单独授权本批次费用上限")
            if supplied.currency != estimated["currency"] or supplied.max_cost < float(estimated.get("reservation_amount", estimated["amount"])):
                raise HTTPException(428, {"message": "授权币种或预算不足以覆盖本次预留金额", "quote": estimated})
            grant = {**supplied.model_dump(), "approved": True, "revision": project.revision,
                     "estimated_cost": estimated, "authorized_at": utcnow().isoformat()}
    snapshot = _job_snapshot(db, project, payload, estimated)
    runtime_job = {"kind": payload.kind, "scene_id": payload.scene_id, "scene_ids": payload.scene_ids,
                   "budget_grant": grant, "revision": project.revision, "estimated_cost": estimated}
    if "_scene_execution" in snapshot:
        runtime_job["execution"] = snapshot["_scene_execution"]
    runtime_module().validate_job(runtime_job, snapshot)
    # Drop the read transaction and its identity-map state before claiming. The
    # quote/execution envelope stays unchanged; fresh authorization is checked
    # again because reviews, revision or missing scenes may have changed meanwhile.
    db.rollback()
    project = db.scalar(select(VideoProject).where(VideoProject.id == project_id,
        VideoProject.owner_id == owner_id).with_for_update().execution_options(populate_existing=True))
    if project is None:
        raise HTTPException(404, "视频项目不存在")
    existing = db.scalar(select(VideoProjectJob).where(VideoProjectJob.project_id == project.id,
        VideoProjectJob.idempotency_key == payload.idempotency_key).execution_options(populate_existing=True))
    if existing:
        if not _same_job_request(existing, payload):
            raise HTTPException(409, "同一个幂等键不能用于不同任务")
        result = _job_dto(existing)
        db.rollback()
        return result
    _revision(project, payload.expected_revision)
    claimed = db.execute(update(VideoProject).where(VideoProject.id == project.id,
        VideoProject.owner_id == owner_id, VideoProject.revision == payload.expected_revision).values(updated_at=utcnow()))
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "方案已变更，请重新加载")
    # A competing request may have committed while this one was quoting. This
    # second lookup occurs after SQLite's write claim as well as MySQL's row lock.
    existing = db.scalar(select(VideoProjectJob).where(VideoProjectJob.project_id == project.id,
        VideoProjectJob.idempotency_key == payload.idempotency_key).execution_options(populate_existing=True))
    if existing:
        if not _same_job_request(existing, payload):
            raise HTTPException(409, "同一个幂等键不能用于不同任务")
        result = _job_dto(existing)
        db.rollback()
        return result
    _check_active_jobs(project, payload, db, locked=True)
    _check_job_inputs(project, payload, db)
    snapshot = _job_snapshot(db, project, payload, estimated)
    job = VideoProjectJob(id=str(uuid4()), project_id=project.id, revision=project.revision, kind=payload.kind,
                          scene_id=payload.scene_id, idempotency_key=payload.idempotency_key, request_hash=fingerprint,
                          status="queued", result={"scene_ids": payload.scene_ids} if payload.kind == "scene_batch" else {},
                          estimated_cost=estimated, budget_grant=grant, snapshot=snapshot)
    db.add(job)
    invalidated = ["voice", "generation", "postprocess", "video"] if payload.kind == "voice" else ["postprocess", "video"] if payload.kind == "avatar" else ["video"]
    db.execute(update(VideoProjectReview).where(VideoProjectReview.project_id == project.id,
        VideoProjectReview.revision == project.revision, VideoProjectReview.stage.in_(invalidated)).values(decision="invalidated"))
    project.status = "producing"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(VideoProjectJob).where(VideoProjectJob.project_id == project.id, VideoProjectJob.idempotency_key == payload.idempotency_key))
        if existing and _same_job_request(existing, payload):
            return _job_dto(existing)
        raise HTTPException(409, "任务已更新，请重新加载")
    background.add_task(_execute_job, job.id, db.get_bind())
    return _job_dto(job)


def _owned_job(db: Session, project: VideoProject, job_id: str) -> VideoProjectJob:
    job = db.scalar(select(VideoProjectJob).where(VideoProjectJob.id == job_id, VideoProjectJob.project_id == project.id))
    if job is None:
        raise HTTPException(404, "视频任务不存在")
    return job


@router.get("/{project_id}/jobs/{job_id}")
def get_job(project_id: str, job_id: str, db: Session = Depends(get_db), principal: AuthPrincipal = Depends(require_api_key)):
    return _job_dto(_owned_job(db, _project(db, project_id, _owner(principal)), job_id))


@router.post("/{project_id}/jobs/{job_id}/refresh")
def refresh_job(project_id: str, job_id: str, background: BackgroundTasks, db: Session = Depends(get_db), principal: AuthPrincipal = Depends(require_api_key)):
    project = _project(db, project_id, _owner(principal))
    job = _owned_job(db, project, job_id)
    if job.status == "succeeded":
        return _job_dto(job)
    if not job.provider_task_id and job.kind in {"avatar", "scene_batch"}:
        # The controlled subprocess may have persisted the provider receipt just
        # before its host process stopped. Reading it cannot submit another task.
        recover = getattr(runtime_module(), "recover_receipt", None)
        receipt = recover(_job_dto(job), job.snapshot, _workdir(project, job.revision)) if recover else None
        if receipt:
            job.provider_task_id = receipt
            if job.status == "blocked":
                job.status = "needs_attention"
            db.commit()
    if job.status not in {"queued", "running", "submitting", "needs_attention"}:
        raise HTTPException(409, "任务已结束；重新制作需要新的生成确认和幂等键")
    if not job.provider_task_id and job.kind != "render":
        raise HTTPException(409, "没有供应商提交回执，已阻止自动重发；请先核对任务与账单")
    if job.lease_token:
        if job.lease_token.startswith(PROCESS_ID + ":"):
            raise HTTPException(409, "任务正在处理，请稍后刷新")
        # A receipt from an earlier process can safely be queried again. No
        # receipt is never recovered by making a second provider submission.
        old_lease = job.lease_token
        released = db.execute(update(VideoProjectJob).where(VideoProjectJob.id == job.id, VideoProjectJob.lease_token == old_lease).values(lease_token=None))
        db.commit()
        if released.rowcount != 1:
            raise HTTPException(409, "任务恢复状态已更新，请重新加载")
    background.add_task(_execute_job, job.id, db.get_bind(), resume=True)
    return _job_dto(job)


def _media_path(project: VideoProject, relative_path: str) -> Path:
    root = _workdir(project).resolve()
    if relative_path.startswith("assets/"):
        root = root.parent
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in {".mp4", ".mov", ".webm", ".wav", ".mp3", ".png", ".jpg", ".jpeg", ".webp", ".srt", ".vtt", ".zip"}:
        raise HTTPException(404, "媒体文件不存在")
    if not path.is_file():
        raise HTTPException(404, "媒体文件不存在")
    return path


@router.get("/{project_id}/files/{relative_path:path}")
def media(project_id: str, relative_path: str, db: Session = Depends(get_db), principal: AuthPrincipal = Depends(require_api_key)):
    project = _project(db, project_id, _owner(principal))
    return FileResponse(_media_path(project, relative_path), headers={"Cache-Control": "private, no-store"})


@router.get("/{project_id}/package")
def publication_package(project_id: str, db: Session = Depends(get_db), principal: AuthPrincipal = Depends(require_api_key)):
    project = _project(db, project_id, _owner(principal))
    _require_reviews(db, project, ["video"])
    renders = [job for job in _jobs(db, project) if job["kind"] == "render" and job["status"] == "succeeded" and job["revision"] == project.revision]
    if not renders:
        raise HTTPException(428, "当前版本尚无已完成的成片")
    result = renders[-1]["result"]
    candidates = [result.get("path")]
    exports = result.get("exports", {})
    values = exports.values() if isinstance(exports, dict) else exports if isinstance(exports, list) else []
    for value in values:
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, dict):
            candidates.extend(value.get(key) for key in ("path", "cover_path", "subtitle_path", "manifest_path", "publication_path"))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("production-plan.json", json.dumps({"topic": project.topic, "revision": project.revision, "platforms": project.platforms, "plan": project.plan, "reviews": _reviews(db, project)}, ensure_ascii=False, indent=2))
        for relative in dict.fromkeys(path for path in candidates if path):
            # Export JSON is generated by the renderer, not arbitrary project
            # files. Keep provenance manifests in the final platform packages.
            if relative.endswith(("/manifest.json", "/publication.md")):
                root = _workdir(project).resolve()
                path = (root / relative).resolve()
                if Path(relative).is_absolute() or ".." in Path(relative).parts or not path.is_relative_to(root) or not path.is_file():
                    raise HTTPException(404, "发布说明文件不存在")
            else:
                path = _media_path(project, relative)
            archive.write(path, arcname=relative)
    output.seek(0)
    return StreamingResponse(output, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="video-{project.id}.zip"', "Cache-Control": "private, no-store"})
