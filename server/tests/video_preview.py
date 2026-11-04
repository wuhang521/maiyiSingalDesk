"""Local browser fixture only: real project API/SQLite; no external providers or .env.

All planner responses, prices, supplier IDs and media below are test doubles.
The generated color-bar MP4 and sine-wave WAV prove playback, not AI quality.
"""
import asyncio
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from types import SimpleNamespace
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config

storage = Path(os.environ["VIDEO_E2E_STORAGE"]).resolve()
storage.mkdir(parents=True, exist_ok=True)
seedance_fixture = os.environ.get("VIDEO_E2E_SEEDANCE") == "1"
runninghub_fixture = not seedance_fixture and os.environ.get("VIDEO_E2E_RUNNINGHUB") == "1"
direct_fixture = seedance_fixture or os.environ.get("VIDEO_E2E_DIRECT") == "1"
scene_provider = "seedance" if seedance_fixture else "runninghub"
scene_model = "doubao-seedance-2-0-mini-260615" if seedance_fixture else "Seedance 2.0 Mini"
if direct_fixture:
    os.environ["TASK_VIDEO_COST_REVIEW_REQUIRED"] = "0"
settings = config.Settings(_env_file=None, database_url=os.environ["TASK_DATABASE_URL"],
    video_storage_dir=str(storage), api_key="video-e2e-only", prompt_api_key="", seedance_api_key="")
config.get_settings = lambda: settings

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.auth import AuthPrincipal, require_api_key
from app.database import Base, SessionLocal, engine
from app.models import Staff, UserAccount
from app import video_projects as api

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:15174"], allow_methods=["*"], allow_headers=["*"])
app.include_router(api.router)

def principal(authorization: str = Header(default="")):
    if authorization != "Bearer video-e2e-only":
        raise HTTPException(401, "仅允许本地视频测试身份")
    return AuthPrincipal(user=SimpleNamespace(id=1))

app.dependency_overrides[require_api_key] = principal

@app.get("/health")
def health():
    return {"status": "test_fixture", "paid_calls": 0}

def make_plan(topic, mode):
    value = {
        "brief": {"title": f"测试方案：{topic}", "audience": "对自然现象好奇的成年人", "core_message": "这是端到端流程测试中的内容，不是发布素材。"},
        "audio_plan": {"narration_style": "清晰平静", "voice": "本地测试正弦波（不是 AI 配音）", "music": "无", "sound_notes": "测试文件不含人声"},
        "persona": {"name": "小知（测试）", "role": "虚构资料讲解员"},
        "visual_style": "清晰图解；测试媒体为纯色片段", "layout": "9:16，底部字幕安全区",
        "platform_variants": {key: {"title": label + "测试标题", "cover_direction": "纯色测试封面，非发布素材", "caption": label + "测试配文", "layout_notes": "9:16统一剪辑测试版"} for key, label in [("weixin", "视频号"), ("douyin", "抖音"), ("xiaohongshu", "小红书")]},
        "postproduction": {"trim_pauses": False, "burn_subtitles": mode != "avatar"},
        "scenes": [
            {"id": "scene-1", "title": "开场疑问（测试）", "duration": 6, "narration": "为什么太阳能一直发光？", "visual_intent": "用太阳场景提出问题", "seedance_prompt": "太阳表面缓慢推进，科学示意，不生成文字"},
            {"id": "scene-2", "title": "回答疑问（测试）", "duration": 6, "narration": "这个测试镜头用来验证答案的安排位置。", "visual_intent": "回答开头问题", "seedance_prompt": "太阳能量示意，固定构图"},
        ],
        "interest_hooks": [{"id": "hook-1", "scene_id": "scene-1", "kind": "opening", "viewer_question": "为什么一直发光", "copy": "太阳会不会熄灭？", "visual": "从日常太阳画面切入", "promise": "解释发光的原因", "payoff_scene_id": "scene-2", "source_refs": []}],
        "medical": {"is_medical": "医疗" in topic, "sources": []},
    }
    if mode == "avatar":
        value["avatar"] = {"motion": "双手轻轻摊开后放回身体两侧", "hands": "双手自然下垂", "posture": "站直", "image_type": "D"}
    return value

async def fake_compose(payload):
    topic = payload.context["topic"]
    if "缺配置" in topic:
        raise HTTPException(503, "测试分支：策划服务未配置，请补充模型凭证")
    plan = copy.deepcopy(payload.context.get("current_plan") or make_plan(topic, payload.context.get("mode")))
    return {"value": plan}

api.compose_prompt = fake_compose

class PreviewRuntime:
    def runtime_capabilities(self):
        capabilities = {key: {"ready": True, "reason": "", "test_double": True} for key in ["planning", "voice", "scene", "avatar", "render"]}
        if runninghub_fixture:
            scene = {"ready": True, "reason": "", "test_double": True, "provider": "runninghub",
                "model": "Seedance 2.0 Mini", "credential_type": "SHARED", "currency": None,
                "supported_currencies": ["CNY", "USD"], "credential_source": "runninghub_shared_key",
                "constraints": {"duration_min": 4, "duration_max": 15, "duration_step": 1,
                    "resolutions": ["480p", "720p"], "ratios": ["9:16"],
                    "default_resolution": "720p", "default_ratio": "9:16"}}
            capabilities.update(scene=scene, scene_batch=copy.deepcopy(scene))
        if seedance_fixture:
            scene = {"ready": True, "reason": "", "test_double": True, "provider": "seedance",
                "model": scene_model, "credential_source": "hermes", "credentials_configured": True,
                "constraints": {"duration_min": 4, "duration_max": 15, "duration_step": 1,
                    "resolutions": ["480p", "720p"], "ratios": ["9:16"],
                    "default_resolution": "720p", "default_ratio": "9:16"}}
            capabilities.update(scene=scene, scene_batch=copy.deepcopy(scene))
        capabilities["avatar"].update(provider="oldx-runninghub", credential_type="SHARED")
        if direct_fixture:
            capabilities["cost_review_required"] = False
        return capabilities

    def estimate_job(self, job, project):
        if direct_fixture:
            raise AssertionError("直接生成模式不应询价")
        result = {"amount": 0 if job["kind"] == "render" else 2.5, "currency": "CNY", "basis": "端到端测试固定虚拟金额；不是真实价格，不产生费用", "scope": "project"}
        if job["kind"] == "scene_batch":
            result.update(scope="scene_batch", scene_ids=job.get("scene_ids"), segment_count=len(job.get("scene_ids") or []))
        if job["kind"] == "avatar":
            result.update(duration_seconds=1, segment_count=1, credential_type="SHARED")
        if runninghub_fixture and job["kind"] in {"scene", "scene_batch"}:
            selected = job.get("scene_ids") or [job.get("scene_id")]
            execution = {"provider": "runninghub", "test_fixture": True, "scene_ids": selected,
                "model": "Seedance 2.0 Mini", "route": "/test-only/runninghub-mini"}
            fingerprint = hashlib.sha256(json.dumps(execution, sort_keys=True).encode()).hexdigest()
            result.update(provider="runninghub", model="Seedance 2.0 Mini", route=execution["route"],
                currency="USD", amount=2.5, reservation_amount=3, credential_type="SHARED",
                provider_hard_cap=False, pricing_kind="estimate", pricing_source="runninghub_price_preview",
                request_fingerprint=fingerprint, execution=execution,
                quoted_at="2026-10-22T00:00:00Z", expires_at="2099-10-22T00:05:00Z",
                source_url="https://www.runninghub.cn/runninghub-api-doc-cn/",
                basis="端到端模拟 RunningHub USD 报价；2.5 美元和 3 美元预留均为桩数据，不是真实价格",
                budget_notice="模拟供应商返回美元，软件预留不是供应商费用硬上限。")
        return result

    def prepare_job(self, job, project):
        selected = job.get("scene_ids") or ([job["scene_id"]] if job.get("scene_id") else [])
        provider = scene_provider if job["kind"] in {"scene", "scene_batch"} else "oldx-runninghub" if job["kind"] == "avatar" else "oldx-bailian" if job["kind"] == "voice" else "local"
        result = {"cost_review_required": False, "provider": provider,
            "scope": "scene_batch" if job["kind"] == "scene_batch" else "scene" if job["kind"] == "scene" else "project"}
        if job["kind"] in {"scene", "scene_batch"}:
            result.update(scene_ids=selected, model=scene_model,
                execution={"schema": f"{scene_provider}-scene/v1", "provider": scene_provider, "test_fixture": True,
                    "model": scene_model, "scenes": {identifier: {"test_fixture": True, "route": f"/test-only/{scene_provider}-mini"} for identifier in selected}})
        if job["kind"] == "avatar":
            result.update(duration_seconds=1, segment_count=1, credential_type="SHARED")
        return result

    def validate_job(self, job, project):
        if direct_fixture and job["kind"] in {"scene", "scene_batch"}:
            if job.get("expected_provider") not in {None, scene_provider}:
                raise AssertionError("本地测试：普通视频通道不匹配")
            if job.get("execution", {}).get("provider") != scene_provider:
                raise AssertionError("本地测试：普通视频固定执行通道不匹配")
        if runninghub_fixture and job["kind"] in {"scene", "scene_batch"}:
            if job.get("execution") != job.get("estimated_cost", {}).get("execution"):
                raise AssertionError("本地测试：报价对应的固定执行参数未保留")
            if not direct_fixture and (job["budget_grant"].get("currency") != "USD" or job["budget_grant"].get("max_cost", 0) < 3):
                raise AssertionError("本地测试：美元软件预留未得到授权")
        return None

    async def execute_job(self, job, project, workdir, progress_callback):
        await progress_callback({"provider_task_id": "LOCAL-TEST-ONLY", "status": "running"})
        await asyncio.sleep(.08)
        if job["kind"] == "voice":
            with wave.open(str(workdir / "voice.wav"), "wb") as stream:
                stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
                stream.writeframes(b"".join(struct.pack("<h", int(1800 * math.sin(2 * math.pi * 440 * n / 16000))) for n in range(16000)))
            result = {"audio_path": "voice.wav", "duration": 1}
            if project["mode"] == "avatar":
                result["avatar_preflight"] = {"scope": "project", "total_seconds": 1, "segment_count": 1, "segments": [{"index": 1, "start": 0, "end": 1, "duration": 1}]}
        elif job["kind"] == "scene_batch":
            identifiers = job.get("scene_ids") or project.get("_scene_batch_ids") or [scene["id"] for scene in project["plan"]["scenes"]]
            results = []
            for identifier in identifiers:
                name = identifier + ".mp4"
                shutil.copy2(storage / "fixture.mp4", workdir / name)
                results.append({"scene_id": identifier, "status": "succeeded", "provider_task_id": "LOCAL-TEST-" + identifier, "submission_started": True, "path": name})
            result = {"scene_ids": identifiers, "scene_results": results}
        else:
            name = "final.mp4" if job["kind"] == "render" else f"{job.get('scene_id') or 'avatar'}.mp4"
            shutil.copy2(storage / "fixture.mp4", workdir / name)
            result = {"path": name}
            if job["kind"] == "render":
                result["exports"] = {platform: name for platform in project["platforms"]}
        if runninghub_fixture and job["kind"] in {"scene", "scene_batch"}:
            result.update(provider="runninghub", billing={"currency": "USD", "known_cost": 2.5,
                "complete": True, "source": "provider_report", "note": "固定模拟回执，无真实扣费"})
        if seedance_fixture and job["kind"] in {"scene", "scene_batch"}:
            result.update(provider="seedance", model=scene_model)
        if job["kind"] == "avatar":
            result.update(provider="oldx-runninghub")
        return {"status": "succeeded", "provider_task_id": "LOCAL-TEST-ONLY", "result": {**result, "test_fixture": True}}

api.runtime_module = lambda: PreviewRuntime()
api._capabilities = lambda: PreviewRuntime().runtime_capabilities()
Base.metadata.create_all(engine)
with SessionLocal() as db:
    if not db.get(UserAccount, 1):
        db.add(UserAccount(id=1, username="video-e2e", password_hash="unused-local-test", staff=Staff(name="视频测试员")))
        db.commit()

if not (storage / "fixture.mp4").is_file():
    candidates = list(Path("C:/Users/86173/.codex/environments/oldx-avatar/Lib/site-packages/imageio_ffmpeg/binaries").glob("ffmpeg*.exe"))
    ffmpeg = str(candidates[0]) if candidates else shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("本地浏览器测试需要 ffmpeg，以创建真实可解码的纯色测试片段")
    subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=0x2f735b:s=240x426:d=1", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(storage / "fixture.mp4")], check=True, capture_output=True)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=18778, log_level="warning")
