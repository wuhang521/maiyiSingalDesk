"""Dedicated serial worker. Lease + revision fencing makes crashes/replays safe."""
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from sqlalchemy import or_, select, update

from .config import get_settings
from .database import Base, SessionLocal, engine
from .knowledge_extract import Extraction
from . import knowledge_ocr, knowledge_asr, knowledge_video_ocr
from .knowledge_cleanup import cleanup_once, expire_trash_once
from .knowledge_models import KnowledgeAsset, KnowledgeItem, KnowledgeJob
from .knowledge_service import PAID_RECOGNITION_MODES, apply_extraction, new_id
from .models import UserAccount, utcnow
from .knowledge_notifications import queue_completion, start_heartbeat

LEASE_SECONDS = 1200


def claim(factory=SessionLocal):
    with factory() as db:
        now = utcnow()
        candidates = db.scalars(select(KnowledgeJob).where(or_(
            (KnowledgeJob.state.in_(["queued", "retry_wait"])) & (KnowledgeJob.available_at <= now),
            (KnowledgeJob.state == "running") & (KnowledgeJob.lease_until < now),
        )).order_by(KnowledgeJob.created_at).limit(20)).all()
        for job in candidates:
            # All code paths acquire item -> job, avoiding an upload/claim deadlock.
            item = db.scalar(select(KnowledgeItem).where(KnowledgeItem.id == job.item_id).with_for_update())
            token = new_id()
            acquired = db.execute(update(KnowledgeJob).where(KnowledgeJob.id == job.id, or_(
                (KnowledgeJob.state.in_(["queued", "retry_wait"])) & (KnowledgeJob.available_at <= now),
                (KnowledgeJob.state == "running") & (KnowledgeJob.lease_until < now),
            )).values(state="running", lease_token=token, lease_until=now + timedelta(seconds=LEASE_SECONDS), attempts=KnowledgeJob.attempts + 1))
            if not acquired.rowcount:
                continue
            db.flush()
            db.refresh(job)
            if not item or item.deleted_at or item.revision != job.revision:
                job.state = "cancelled"
                db.commit()
                continue
            if job.attempts > 3:
                job.state, item.status = "failed", "failed"
                item.status_note = "识别进程多次中断，已停止自动重试。原始素材已保留，请手动重试或补充正文。"
                item.updated_at = now
                queue_completion(db, item, job.id)
                db.commit()
                continue
            if job.mode in PAID_RECOGNITION_MODES:
                user = db.get(UserAccount, item.owner_id)
                if not user or not user.enabled or not user.staff.enabled or user.staff.removed_at:
                    job.state, item.status = "cancelled", "needs_input"
                    item.status_note = "账号已停用，未继续云端识别；原件已保留。"
                    db.commit()
                    continue
                if job.attempts > 1:
                    job.state, item.status = "needs_input", "needs_input"
                    item.status_note = "云端识别进程中断，结果无法确认；未自动再次提交。再次识别可能再次计费，原件已保留。"
                    queue_completion(db, item, job.id)
                    db.commit()
                    continue
            asset = db.get(KnowledgeAsset, job.asset_id) if job.asset_id else None
            request = dict(url=item.source_url)
            if asset:
                root = Path(get_settings().knowledge_storage_dir).resolve()
                path = (root / asset.storage_name).resolve()
                if path.parent != root:
                    job.state, item.status = "failed", "failed"
                    item.status_note = "原始文件位置无效，请联系管理员。"
                    db.commit()
                    continue
                request = dict(path=str(path), filename=asset.filename, sha256=asset.sha256)
            request["mode"] = job.mode
            request["request_id"] = job.id
            request["fence"] = dict(item_id=item.id, revision=job.revision, job_id=job.id, token=token)
            item.status, item.status_note = "extracting", "正在进行云端识别，原件已保存；完成后需核对。" if job.mode in PAID_RECOGNITION_MODES else "正在提取内容，原始素材已保存。"
            item.updated_at = now
            db.commit()
            return dict(id=job.id, item_id=item.id, revision=job.revision, token=token, attempts=job.attempts, request=request)
    return None


def extract(request, factory=SessionLocal):
    def may_send():
        fence=request.get("fence")
        if not fence:
            return False
        with factory() as db:
            item=db.get(KnowledgeItem,fence["item_id"])
            job=db.get(KnowledgeJob,fence["job_id"])
            user=db.get(UserAccount,item.owner_id) if item else None
            return bool(item and not item.deleted_at and item.revision==fence["revision"] and
                job and job.state=="running" and job.lease_token==fence["token"] and
                job.lease_until and job.lease_until>=utcnow() and user and user.enabled and
                user.staff.enabled and not user.staff.removed_at)
    if request.get("path"):
        path = Path(request["path"])
        if not path.is_file():
            return dict(state="failed", note="原始文件缺失，请联系管理员检查备份。")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != request["sha256"]:
            return dict(state="failed", note="原始文件校验失败，请重新上传。")
    if request.get("mode") == "volc_asr":
        if not request.get("path"):
            return dict(state="needs_input", note="转写原件不存在，请重新上传素材。")
        try:
            result = knowledge_asr.recognize(Path(request["path"]), request["filename"], request["request_id"],may_send=may_send)
            return dict(state="ready", text=result.text, method=result.method, chunks=result.chunks)
        except knowledge_asr.AsrError as error:
            return dict(state="needs_input", note=str(error))
        except Exception:
            return dict(state="needs_input", note="云端转写结果无法确认，未自动重试；原件已保留，再次识别可能再次计费。")
    if request.get("mode") == "video_ocr":
        if not request.get("path"):
            return dict(state="needs_input",note="视频原件不存在，请重新上传素材。")
        try:
            result=knowledge_video_ocr.recognize(Path(request["path"]),request["filename"],may_send)
            return dict(state="ready",text=result.text,method=result.method,chunks=result.chunks)
        except knowledge_ocr.OcrError as error:
            return dict(state="needs_input",note=str(error))
        except Exception:
            return dict(state="needs_input",note="画面 OCR 结果未确认，已停止后续请求；不自动重试，再次识别可能再次计费。")
    if request.get("mode") == "doubao_ocr":
        if not request.get("path"):
            return dict(state="needs_input", note="OCR 原件不存在，请重新上传。")
        remote_files = []
        def remember(file_id):
            knowledge_ocr.record_cleanup(file_id, factory)
            remote_files.append(file_id)
        try:
            result = knowledge_ocr.recognize(Path(request["path"]), request["filename"],
                                            remember, may_send=may_send)
            return dict(state="ready", text=result.text, method=result.method, chunks=result.chunks)
        except knowledge_ocr.OcrError as error:
            return dict(state="needs_input", note=str(error))
        except Exception:
            return dict(state="needs_input", note="OCR 结果无法确认，未自动重试；原件已保留。再次识别可能再次计费。")
        finally:
            for file_id in remote_files:
                knowledge_ocr.cleanup_remote_once(factory, file_id=file_id)
    try:
        output = subprocess.run([sys.executable, "-m", "app.knowledge_extract_cli"], input=json.dumps(request),
                                text=True, encoding="utf-8", capture_output=True, timeout=960, check=True,
                                cwd=Path(__file__).resolve().parents[1], env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if len(output.stdout) > 5_000_000:
            return dict(state="failed", note="提取结果超过限制，请拆分后导入。")
        return json.loads(output.stdout)
    except subprocess.TimeoutExpired:
        return dict(state="failed", note="识别超时，已停止处理。原始素材已保留，请拆分或补充字幕后重试。")
    except (subprocess.SubprocessError, ValueError, OSError):
        return dict(state="failed", note="识别进程失败，原始素材已保留，请联系管理员或补充正文。")


def finish(job, result, factory=SessionLocal):
    with factory() as db:
        # Lock item before the final job check, matching edit/delete/upload lock order.
        item = db.scalar(select(KnowledgeItem).where(KnowledgeItem.id == job["item_id"]).with_for_update())
        current = db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job["id"], KnowledgeJob.state == "running",
                            KnowledgeJob.lease_token == job["token"]).with_for_update())
        if not current:
            return False
        if not item or item.deleted_at or item.revision != job["revision"]:
            current.state = "cancelled"
            db.commit()
            return False
        if current.mode in PAID_RECOGNITION_MODES:
            user = db.get(UserAccount, item.owner_id)
            if not user or not user.enabled or not user.staff.enabled or user.staff.removed_at:
                current.state, item.status = "cancelled", "needs_input"
                item.status_note = "识别期间账号被停用，未采用返回正文；原件已保留。"
                item.updated_at = utcnow()
                db.commit()
                return False
        if result["state"] == "ready":
            if current.mode in {"volc_asr", "video_ocr"} and current.asset_id:
                from .knowledge_models import KnowledgeMediaPart
                db.add(KnowledgeMediaPart(id=new_id(), item_id=item.id, asset_id=current.asset_id, source_revision=item.revision,
                    method=result["method"], content=result["text"], chunks=result["chunks"]))
            apply_extraction(db, item, Extraction(result["text"], result["method"], result["chunks"], result.get("title", ""), result.get("publisher", "")))
            if current.mode in PAID_RECOGNITION_MODES:
                item.status = "needs_review"
                item.status_note = "AI 识别正文待核对，暂不参与检索或问答。请核对原件，修订正文或确认入库。"
            current.state = "done"
        else:
            state = result["state"]
            if state == "retry_wait" and current.mode in PAID_RECOGNITION_MODES:
                state = "needs_input"
            if state == "retry_wait" and current.attempts >= 3:
                state = "failed"
            current.state, item.status = state, state
            item.status_note = result["note"][:500]
            if state == "retry_wait":
                current.available_at = utcnow() + timedelta(seconds=30 * 2 ** (current.attempts - 1))
        current.lease_until, current.lease_token = None, None
        item.updated_at = utcnow()
        queue_completion(db, item, current.id)
        db.commit()
        return True


def run_once(factory=SessionLocal, extractor=extract):
    remote_cleaned = knowledge_ocr.cleanup_remote_once(factory)
    cleaned = cleanup_once(factory)
    expired = expire_trash_once(factory)
    job = claim(factory)
    if not job:
        return cleaned or expired or remote_cleaned
    from .knowledge_provider_trace import span
    with span(job["id"], factory):
        result = extract(job["request"], factory) if extractor is extract else extractor(job["request"])
    finish(job, result, factory)
    return True


def main():
    Base.metadata.create_all(engine)
    stopped, activity = start_heartbeat("extract")
    try:
        while True:
            try:
                activity["state"] = "busy"
                worked = run_once(); activity["state"] = "idle"
                if not worked: time.sleep(3)
            except KeyboardInterrupt: return
            except Exception:
                activity["state"] = "error"
                print("knowledge_worker: processing failed; lease recovery remains available", file=sys.stderr, flush=True)
                time.sleep(5)
    finally: stopped.set()


if __name__ == "__main__":
    main()
