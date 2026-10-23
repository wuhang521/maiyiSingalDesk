"""Disposable local backend + real dedicated worker for desktop E2E."""
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
database = os.environ.get("TASK_DATABASE_URL", "")
if not database.startswith("sqlite:///") or not database.endswith("/knowledge-e2e.db"):
    raise RuntimeError("Requires an isolated knowledge-e2e.db")
if Path(database.removeprefix("sqlite:///")).exists():
    raise RuntimeError("Refusing to overwrite existing preview data")
if os.environ.get("TASK_KNOWLEDGE_ASR_ENABLED", "").lower() != "false":
    raise RuntimeError("E2E must not run real ASR")

from app.main import app
from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.knowledge import ItemCreate
from app.knowledge_service import create_item
from app.knowledge_worker import run_once
from app import knowledge_cloud, knowledge_ocr
from app.config import get_settings
import httpx
import json
from fastapi import Body
from app.models import Staff, UserAccount
import uvicorn

Base.metadata.create_all(engine)
with SessionLocal() as db:
    user = UserAccount(username="preview-kb", staff=Staff(name="本地知识库测试用户"), role="member", password_hash=hash_password("preview-only-password"))
    db.add(user); db.commit()
    create_item(db, user.id, ItemCreate(title="演示：健康管理笔记", publisher="本地测试材料（非真实公众号）", tags=["健康管理"],
                                      content="以下为软件测试虚构资料，不代表医疗建议。\n\n健康管理素材的收集方式：先保留来源，再保存原文，最后核对引用位置。\n\n每份资料都要记录出处和正文版本。"))
    create_item(db, user.id, ItemCreate(source_url="https://weixin.qq.com/sph/preview-only", title="演示：待补充的视频号素材"))

stop = threading.Event()
cloud_calls = []
cloud_failure = False
ocr_calls = []
ocr_failure = False

def fake_ocr(request):
    ocr_calls.append(request.method + " " + request.url.path)
    if ocr_failure:
        return httpx.Response(500, json={"error": "synthetic failure"})
    return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({
        "pages": [{"page": 1, "text": "软件测试模拟 OCR：截图归档先保留原件，再核对正文。此文字是合成测试资料，不代表真实识别结果。"}]
    }, ensure_ascii=False)}]}]})

original_recognize = knowledge_ocr.recognize
knowledge_ocr.recognize = lambda path, filename, remember, **kwargs: original_recognize(path, filename, remember, transport=httpx.MockTransport(fake_ocr), **kwargs)
knowledge_ocr.delete_remote = lambda _: True
get_settings().knowledge_ocr_enabled = False
get_settings().knowledge_ocr_api_key = ""

@app.post("/preview-ocr/mode")
def ocr_mode(failure: bool = Body(default=False, embed=True)):
    global ocr_failure
    ocr_failure = failure
    get_settings().knowledge_ocr_enabled = True
    get_settings().knowledge_ocr_api_key = "synthetic-test-key"
    return {"calls": len(ocr_calls)}

@app.get("/preview-ocr/calls")
def ocr_request_count():
    return {"calls": len(ocr_calls)}
def fake_cloud(request):
    payload = json.loads(request.content)
    cloud_calls.append(payload)
    if cloud_failure:
        return httpx.Response(500, json={"error": "synthetic failure"})
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
        "insufficient": False, "statements": [{"text": "软件测试模拟综合：先保存来源，再核对原文。", "citations": [1]}]
    }, ensure_ascii=False)}}]})
# Even with ambient credentials, this disposable preview can never call a real model.
knowledge_cloud.cloud_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake_cloud), follow_redirects=False, trust_env=False)
get_settings().knowledge_cloud_qa_enabled = False

@app.post("/preview-cloud/mode")
def cloud_mode(failure: bool = Body(default=False, embed=True)):
    global cloud_failure
    cloud_failure = failure
    s = get_settings()
    s.knowledge_cloud_qa_enabled = True
    s.prompt_api_key, s.prompt_model, s.prompt_base_url = "synthetic-test-key", "synthetic-model", "https://api.deepseek.com"
    knowledge_cloud.admission = knowledge_cloud.Admission()
    return {"calls": len(cloud_calls)}

@app.get("/preview-cloud/calls")
def cloud_request_count():
    return {"calls": len(cloud_calls)}

def work():
    while not stop.is_set():
        try:
            run_once()
        except Exception:
            print("preview worker failed", file=sys.stderr, flush=True)
        stop.wait(.3)
thread = threading.Thread(target=work, daemon=True)
thread.start()

@app.get("/preview-release")
def release():
    return dict(tag_name="v0.5.0", assets=[], html_url="http://127.0.0.1/")

try:
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
finally:
    stop.set()
