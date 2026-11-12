"""Opt-in PaddleOCR transcription. No redirects, automatic retries, or Ark uploads."""
import base64
from datetime import timedelta
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .knowledge_extract import Extraction, IMAGE_EXTENSIONS
from .knowledge_models import KnowledgeRemoteCleanup
from .knowledge_service import new_id
from .models import utcnow

# Only legacy cleanup uses Ark; new OCR sends one Base64 request to PaddleOCR.
ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
OCR_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}
MAX_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
FILE_ID = re.compile(r"file-[A-Za-z0-9_-]{1,150}\Z")
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


class OcrError(Exception):
    """Safe messages only; provider bodies and credentials must never reach logs/UI."""


def configuration():
    s = get_settings()
    try:
        url = urlsplit(s.paddle_ocr_url.strip())
        valid_url = (not any(char.isspace() for char in s.paddle_ocr_url.strip()) and
                     url.scheme in {"http", "https"} and bool(url.hostname) and
                     url.path.rstrip("/").endswith("/ocr") and
                     not url.username and not url.password and not url.fragment)
        _ = url.port  # Reject malformed port strings before enabling the UI.
    except ValueError:
        return False
    token = s.paddle_ocr_auth_token.strip()
    valid_auth = not token or (token.isascii() and bool(HEADER_NAME.fullmatch(s.paddle_ocr_auth_header.strip())) and
                               "\r" not in token and "\n" not in token)
    return bool(s.knowledge_ocr_enabled and valid_url and valid_auth)


def preflight(path, filename):
    extension = Path(filename).suffix.lower()
    if extension not in OCR_EXTENSIONS or not 0 < path.stat().st_size <= MAX_BYTES:
        raise OcrError("OCR 只支持不超过 20 MB 的 JPG、PNG 或 PDF。原件已保留。")
    with path.open("rb") as stream:
        signature = stream.read(8)
    if extension == ".pdf":
        if not signature.startswith(b"%PDF-"):
            raise OcrError("文件不是有效 PDF，请检查原件。")
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise ValueError()
            pages = len(reader.pages)
        except Exception:
            raise OcrError("PDF 无法读取或已加密，请解密后重新上传。") from None
        if not 1 <= pages <= get_settings().knowledge_ocr_max_pdf_pages:
            raise OcrError(f"OCR 每份 PDF 最多 {get_settings().knowledge_ocr_max_pdf_pages} 页，请拆分后识别。")
        return extension, pages
    if (extension == ".png" and signature != b"\x89PNG\r\n\x1a\n") or (extension in {".jpg", ".jpeg"} and not signature.startswith(b"\xff\xd8\xff")):
        raise OcrError("图片类型与内容不符，请重新导出 JPG / PNG。")
    return extension, 1


def request_json(client, url, deadline, connect_timeout, check_authorization, **kwargs):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OcrError("OCR 超时，未自动重试；原件已保留。")
    check_authorization()
    try:
        with client.stream("POST", url, timeout=httpx.Timeout(remaining, connect=min(connect_timeout, remaining)), **kwargs) as response:
            if response.status_code not in range(200, 300):
                raise OcrError("PaddleOCR 请求失败，未自动重试；请检查服务地址、鉴权或服务状态。")
            body = bytearray()
            for block in response.iter_bytes():
                check_authorization()
                body.extend(block)
                if len(body) > MAX_RESPONSE_BYTES or time.monotonic() > deadline:
                    raise OcrError("OCR 结果超限或超时，未自动重试；请拆分文件。")
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError()
            return data
    except (httpx.HTTPError, ValueError, UnicodeError):
        raise OcrError("PaddleOCR 结果未能确认，未自动重试；原件已保留。") from None


def decode_result(data, page_count, *, allow_empty=False):
    if type(data.get("errorCode")) is not int or data["errorCode"] != 0:
        raise OcrError("PaddleOCR 未完整完成，未采用结果；请检查服务状态或拆分文件。")
    try:
        results = data["result"]["ocrResults"]
        if not isinstance(results, list) or len(results) != page_count:
            raise ValueError()
        pruned = [entry["prunedResult"] for entry in results]
        if any(not isinstance(page, dict) for page in pruned):
            raise ValueError()
        indexes = [page.get("page_index") for page in pruned]
        if any(index is not None for index in indexes):
            if any(type(index) is not int for index in indexes) or sorted(indexes) != list(range(page_count)):
                raise ValueError()
            pruned = [page for _, page in sorted(zip(indexes, pruned), key=lambda pair: pair[0])]
        pages = []
        for number, page in enumerate(pruned, 1):
            lines = page["rec_texts"]
            if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
                raise ValueError()
            text = "\n".join(lines).strip()
            if len(text) > 100_000:
                raise ValueError()
            pages.append({"page": number, "text": text})
        body = "\n\n".join(page["text"] for page in pages).strip()
        if (not body and not allow_empty) or len(body) > 300_000:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise OcrError("OCR 没有返回完整可核对的逐页正文，未入库；请拆分或手动补充。") from None
    chunks = [(f"第 {page['page']} 页（AI OCR）", page["text"][i:i + 900]) for page in pages
              for i in range(0, len(page["text"]), 900) if page["text"][i:i + 900].strip()]
    # Persisted legacy method keeps draft confirmation and history compatible.
    return Extraction(body, "doubao_ocr_draft", chunks)


def recognize(path, filename, remember_file, *, transport=None, allow_empty=False, may_send=lambda: True):
    if not configuration():
        raise OcrError("PaddleOCR 尚未启用或服务地址配置无效，原件已保留；请联系管理员。")
    extension, pages = preflight(path, filename)
    settings = get_settings()
    deadline = time.monotonic() + settings.paddle_ocr_timeout_seconds

    def check_authorization():
        if not may_send():
            raise OcrError("识别期间账号、资料或任务改变，已停止后续 OCR 请求；原件已保留。")

    check_authorization()
    payload = {"file": base64.b64encode(path.read_bytes()).decode("ascii"),
               "fileType": 0 if extension == ".pdf" else 1, "visualize": False}
    headers = {}
    if settings.paddle_ocr_auth_token.strip():
        headers[settings.paddle_ocr_auth_header.strip()] = settings.paddle_ocr_auth_token.strip()
    # No provider file ID is created, so remember_file must not register Ark cleanup.
    with httpx.Client(headers=headers, follow_redirects=False, trust_env=False, transport=transport) as client:
        data = request_json(client, settings.paddle_ocr_url.strip().rstrip("/"), deadline,
                            settings.paddle_ocr_connect_timeout_seconds, check_authorization, json=payload)
    check_authorization()
    return decode_result(data, pages, allow_empty=allow_empty)


def record_cleanup(file_id, factory=SessionLocal):
    """Compatibility entry for migration-era jobs; PaddleOCR never calls this."""
    if not FILE_ID.fullmatch(file_id):
        raise ValueError("Invalid provider file id")
    with factory() as db:
        db.add(KnowledgeRemoteCleanup(id=new_id(), provider_file_id=file_id,
                                      available_at=utcnow() + timedelta(minutes=10)))
        db.commit()


def delete_remote(file_id, *, transport=None):
    """Delete only an already recorded legacy Ark upload; never upload or infer."""
    key = get_settings().knowledge_ocr_api_key.strip()
    if not key or not FILE_ID.fullmatch(file_id):
        return False
    try:
        with httpx.Client(headers={"Authorization": f"Bearer {key}"}, follow_redirects=False,
                          trust_env=False, timeout=httpx.Timeout(10, connect=5), transport=transport) as client:
            with client.stream("DELETE", f"{ARK_BASE}/files/{file_id}") as response:
                return 200 <= response.status_code < 300 or response.status_code in {404, 410}
    except httpx.HTTPError:
        return False


def cleanup_remote_once(factory=SessionLocal, file_id=None):
    # Only pre-existing legacy rows cause a provider call. New PaddleOCR has none.
    with factory() as db:
        row = db.scalar(select(KnowledgeRemoteCleanup).where(
                        KnowledgeRemoteCleanup.provider_file_id == file_id if file_id else KnowledgeRemoteCleanup.available_at <= utcnow())
                        .order_by(KnowledgeRemoteCleanup.created_at).limit(1))
        if not row:
            return False
        row_id, file_id = row.id, row.provider_file_id
    success = delete_remote(file_id)
    with factory() as db:
        row = db.get(KnowledgeRemoteCleanup, row_id)
        if row:
            if success:
                db.delete(row)
            else:
                row.attempts += 1
                row.available_at = utcnow() + timedelta(seconds=min(3600, 30 * 2 ** min(row.attempts, 7)))
            db.commit()
    return True

