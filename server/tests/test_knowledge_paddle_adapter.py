"""Paddle adapter contract tests; synthetic files and HTTP transport, no network."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("TASK_DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from pypdf import PdfWriter
from app import knowledge_ocr, knowledge_video_ocr
from app.config import Settings, get_settings


def result(*pages, indexes=None):
    return {"errorCode": 0, "result": {"ocrResults": [
        {"prunedResult": {"rec_texts": lines, "page_index": i if indexes is None else indexes[i]}}
        for i, lines in enumerate(pages)]}}


class PaddleAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.settings = get_settings()
        values = dict(knowledge_ocr_enabled=True, paddle_ocr_url="http://paddle-ocr.test/ocr",
                      paddle_ocr_auth_header="Authorization", paddle_ocr_auth_token="",
                      paddle_ocr_timeout_seconds=180, paddle_ocr_connect_timeout_seconds=5,
                      knowledge_ocr_api_key="", knowledge_ocr_model="")
        for key, value in values.items():
            context = patch.object(self.settings, key, value)
            context.start()
            self.addCleanup(context.stop)
        self.image = self.directory / "private.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")

    def recognize(self, response, **kwargs):
        return knowledge_ocr.recognize(self.image, self.image.name, lambda _: self.fail("Ark file registered"),
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)), **kwargs)

    def pdf(self, pages):
        path = self.directory / "private.pdf"
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=100, height=100)
        with path.open("wb") as stream:
            writer.write(stream)
        return path

    def test_configuration_needs_enabled_url_but_no_ark_key(self):
        self.assertTrue(knowledge_ocr.configuration())
        for url in ["", "file:///ocr", "http://user:password@paddle.test/ocr", "http://paddle.test:bad/ocr", "http://paddle.test/responses"]:
            with self.subTest(url=url), patch.object(self.settings, "paddle_ocr_url", url):
                self.assertFalse(knowledge_ocr.configuration())
        with patch.object(self.settings, "knowledge_ocr_enabled", False):
            self.assertFalse(knowledge_ocr.configuration())

    def test_environment_aliases_and_timeout_validation(self):
        with patch.dict(os.environ, {"PADDLE_OCR_URL": "https://paddle.test/ocr", "PADDLE_OCR_TIMEOUT_SECONDS": "240"}):
            settings = Settings(_env_file=None)
            self.assertEqual(settings.paddle_ocr_url, "https://paddle.test/ocr")
            self.assertEqual(settings.paddle_ocr_timeout_seconds, 240)
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, paddle_ocr_timeout_seconds=0)

    def test_image_request_is_single_private_base64_post(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(str(request.url), "http://paddle-ocr.test/ocr")
            self.assertEqual(request.method, "POST")
            self.assertNotIn("Authorization", request.headers)
            payload = json.loads(request.content)
            self.assertEqual(payload, {"file": base64.b64encode(self.image.read_bytes()).decode(), "fileType": 1, "visualize": False})
            self.assertEqual(request.extensions["timeout"]["connect"], 5)
            return httpx.Response(200, json=result(["第一行", "第二行"]))
        output = knowledge_ocr.recognize(self.image, self.image.name, lambda _: self.fail("Ark cleanup called"), transport=httpx.MockTransport(handler))
        self.assertEqual(output.text, "第一行\n第二行")
        self.assertEqual(output.chunks, [("第 1 页（AI OCR）", "第一行\n第二行")])
        self.assertEqual(output.method, "doubao_ocr_draft")
        self.assertEqual(len(requests), 1)

    def test_optional_auth_uses_configured_header_and_verbatim_value(self):
        with patch.object(self.settings, "paddle_ocr_auth_header", "X-API-Key"), patch.object(self.settings, "paddle_ocr_auth_token", "synthetic-test-token"):
            def handler(request):
                self.assertEqual(request.headers["X-API-Key"], "synthetic-test-token")
                self.assertNotIn("Authorization", request.headers)
                return httpx.Response(200, json=result(["测试"]))
            knowledge_ocr.recognize(self.image, self.image.name, lambda _: None, transport=httpx.MockTransport(handler))

    def test_pdf_page_mapping_sorted_and_no_ark_registration(self):
        path = self.pdf(2)
        def handler(request):
            self.assertEqual(str(request.url), "http://paddle-ocr.test/ocr")
            payload = json.loads(request.content)
            self.assertEqual(payload["fileType"], 0)
            self.assertEqual(base64.b64decode(payload["file"]), path.read_bytes())
            return httpx.Response(200, json=result(["第二页"], ["第一页"], indexes=[1, 0]))
        output = knowledge_ocr.recognize(path, path.name, lambda _: self.fail("Ark file registered"), transport=httpx.MockTransport(handler))
        self.assertEqual(output.text, "第一页\n\n第二页")
        self.assertEqual([chunk[0] for chunk in output.chunks], ["第 1 页（AI OCR）", "第 2 页（AI OCR）"])

    def test_pdf_missing_duplicate_or_invalid_pages_never_admitted(self):
        path = self.pdf(2)
        for response in [result(["第一页"]), result(["a"], ["b"], indexes=[0,0]), result(["a"], ["b"], indexes=[1,2]), result(["a"], ["b"], indexes=[0,None])]:
            with self.subTest(response=response), self.assertRaises(knowledge_ocr.OcrError):
                knowledge_ocr.recognize(path, path.name, lambda _: None, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)))

    def test_blank_pdf_pages_keep_original_page_locations(self):
        output = knowledge_ocr.decode_result(result([], ["第二页正文"]), 2)
        self.assertEqual(output.chunks, [("第 2 页（AI OCR）", "第二页正文")])

    def test_empty_text_rejected_except_video_frame_opt_in(self):
        with self.assertRaises(knowledge_ocr.OcrError):
            self.recognize(result([]))
        self.assertEqual(self.recognize(result([]), allow_empty=True).text, "")

    def test_response_size_and_total_deadline_limits(self):
        with patch.object(knowledge_ocr, "MAX_RESPONSE_BYTES", 20), self.assertRaises(knowledge_ocr.OcrError):
            self.recognize(result(["超限结果"]))
        with patch.object(knowledge_ocr.time, "monotonic", side_effect=[0, 181]), self.assertRaises(knowledge_ocr.OcrError):
            knowledge_ocr.recognize(self.image, self.image.name, lambda _: None,
                transport=httpx.MockTransport(lambda _: self.fail("Deadline already expired")))

    def test_invalid_schema_errors_and_oversize_text_are_safe(self):
        invalid = [{}, {"errorCode": 7, "errorMsg": "private-token"}, result([1]), result(["x" * 100001]),
                   {"errorCode": 0, "result": {"ocrResults": [{"prunedResult": []}]}}]
        for response in invalid:
            with self.subTest(case=type(response).__name__), self.assertRaises(knowledge_ocr.OcrError) as raised:
                self.recognize(response)
            self.assertNotIn("private-token", str(raised.exception))

    def test_http_error_invalid_json_timeout_and_redirect_have_no_retry(self):
        cases = [lambda _: httpx.Response(503, text="private-token"),
                 lambda _: httpx.Response(302, headers={"Location": "https://ark.cn-beijing.volces.com/api/v3/responses"}),
                 lambda _: httpx.Response(200, text="not json"),
                 lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("private-token", request=request))]
        for responder in cases:
            requests = []
            def handler(request):
                requests.append(request)
                return responder(request)
            with self.subTest(responder=responder), self.assertRaises(knowledge_ocr.OcrError) as raised:
                knowledge_ocr.recognize(self.image, self.image.name, lambda _: None, transport=httpx.MockTransport(handler))
            self.assertEqual(len(requests), 1)
            self.assertNotIn("private-token", str(raised.exception))

    def test_cancellation_before_send_and_after_response_rejects_result(self):
        requests = []
        transport = httpx.MockTransport(lambda request: (requests.append(request) or httpx.Response(200, json=result(["测试"])) ))
        with self.assertRaises(knowledge_ocr.OcrError):
            knowledge_ocr.recognize(self.image, self.image.name, lambda _: None, transport=transport, may_send=lambda: False)
        self.assertEqual(requests, [])
        state = {"allowed": True}
        def handler(request):
            state["allowed"] = False
            return httpx.Response(200, json=result(["不应采用"]))
        with self.assertRaises(knowledge_ocr.OcrError):
            knowledge_ocr.recognize(self.image, self.image.name, lambda _: None, transport=httpx.MockTransport(handler), may_send=lambda: state["allowed"])

    def test_unsupported_type_signature_size_and_page_limit_stop_before_send(self):
        bad = self.directory / "fake.png"; bad.write_bytes(b"not-a-png")
        oversized = self.directory / "large.png"
        with oversized.open("wb") as stream:
            stream.truncate(20 * 1024 * 1024 + 1)
        for path, filename in [(self.image, "file.gif"), (bad, "fake.png"), (oversized, "large.png"), (self.pdf(11), "long.pdf")]:
            with self.subTest(filename=filename), self.assertRaises(knowledge_ocr.OcrError):
                knowledge_ocr.recognize(path, filename, lambda _: None, transport=httpx.MockTransport(lambda _: self.fail("Unexpected request")))

    def test_legacy_remote_delete_is_delete_only(self):
        requests = []
        with patch.object(self.settings, "knowledge_ocr_api_key", "synthetic-legacy-key"):
            self.assertTrue(knowledge_ocr.delete_remote("file-legacy", transport=httpx.MockTransport(lambda request: (requests.append(request) or httpx.Response(204)))))
        self.assertEqual([(r.method, r.url.path) for r in requests], [("DELETE", "/api/v3/files/file-legacy")])

    def test_cleanup_only_calls_provider_for_persisted_legacy_row(self):
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker
        from app.database import Base
        from app.knowledge_models import KnowledgeRemoteCleanup
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with patch.object(knowledge_ocr, "delete_remote", return_value=True) as delete:
            self.recognize(result(["新Paddle结果"]))
            self.assertFalse(knowledge_ocr.cleanup_remote_once(factory))
            delete.assert_not_called()
            knowledge_ocr.record_cleanup("file-existing-legacy", factory)
            self.assertTrue(knowledge_ocr.cleanup_remote_once(factory, file_id="file-existing-legacy"))
            delete.assert_called_once_with("file-existing-legacy")
        with factory() as db:
            self.assertIsNone(db.scalar(select(KnowledgeRemoteCleanup)))

    def test_video_configuration_depends_on_paddle_url_not_legacy_key(self):
        with patch.object(self.settings, "knowledge_video_ocr_enabled", True), patch.object(knowledge_video_ocr.shutil, "which", return_value="test-binary"):
            self.assertTrue(knowledge_video_ocr.configuration())
            with patch.object(self.settings, "paddle_ocr_url", ""):
                self.assertFalse(knowledge_video_ocr.configuration())

    def test_video_frame_path_reuses_paddle_adapter_and_keeps_time_locator(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(str(request.url), "http://paddle-ocr.test/ocr")
            self.assertEqual(json.loads(request.content)["fileType"], 1)
            return httpx.Response(200, json=result([] if len(requests) == 1 else ["画面文字"]))
        def image_recognizer(*args, **kwargs):
            return knowledge_ocr.recognize(*args, **kwargs, transport=httpx.MockTransport(handler))
        with patch.object(knowledge_video_ocr, "configuration", return_value=True):
            output = knowledge_video_ocr.recognize(Path("synthetic.mp4"), "synthetic.mp4", lambda: True,
                frame_extractor=lambda *_: [(0.0, self.image), (8.5, self.image)], image_recognizer=image_recognizer)
        self.assertEqual(len(requests), 2)
        self.assertEqual(output.method, "video_ocr_draft")
        self.assertEqual(output.chunks, [("8.50s 采样画面（AI OCR）", "画面文字")])

    def test_api_worker_draft_confirmation_and_owner_fencing(self):
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from app.auth import create_user_session
        from app.database import Base, get_db
        from app.knowledge_models import KnowledgeRemoteCleanup
        from app.knowledge_worker import claim, extract, finish
        from app.main import app
        from app.models import Staff, UserAccount
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        headers = []
        with factory() as db:
            for n in range(2):
                user = UserAccount(username=f"paddle-test-{n}", password_hash="unused", staff=Staff(name=f"测试成员{n}"))
                db.add(user); db.flush()
                token, _ = create_user_session(db, user)
                headers.append({"Authorization": f"Bearer {token}"})
            db.commit()
        def sessions():
            with factory() as db:
                yield db
        app.dependency_overrides[get_db] = sessions
        self.addCleanup(app.dependency_overrides.clear)
        storage = patch.object(self.settings, "knowledge_storage_dir", str(self.directory / "files"))
        storage.start(); self.addCleanup(storage.stop)
        client = TestClient(app)
        self.addCleanup(client.close)
        capability = client.get("/api/v1/knowledge/capabilities", headers=headers[0]).json()
        self.assertTrue(capability["ocr_enabled"])
        self.assertEqual(capability["ocr_provider"], "PaddleOCR / 华为云自建服务")
        created = client.post("/api/v1/knowledge/items", headers=headers[0], json={"title": "合成图片"})
        self.assertEqual(created.status_code, 201, created.text)
        item = created.json()
        uploaded = client.post(f"/api/v1/knowledge/items/{item['id']}/assets", headers=headers[0],
            params={"filename": self.image.name, "revision": item["revision"]}, content=self.image.read_bytes())
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        item = uploaded.json()
        endpoint = f"/api/v1/knowledge/items/{item['id']}/assets/{item['assets'][0]['id']}/ocr"
        payload = {"revision": item["revision"], "cloud_consent": True}
        self.assertEqual(client.post(endpoint, headers=headers[1], json=payload).status_code, 404)
        self.assertEqual(client.post(endpoint, headers=headers[0], json={**payload, "cloud_consent": False}).status_code, 422)
        submitted = client.post(endpoint, headers=headers[0], json=payload)
        self.assertEqual(submitted.status_code, 200, submitted.text)
        job = claim(factory)
        self.assertEqual(job["request"]["mode"], "doubao_ocr")
        original_recognize = knowledge_ocr.recognize
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(str(request.url), "http://paddle-ocr.test/ocr")
            return httpx.Response(200, json=result(["合成健康文字"]))
        def recognize(*args, **kwargs):
            return original_recognize(*args, **kwargs, transport=httpx.MockTransport(handler))
        with patch.object(knowledge_ocr, "recognize", side_effect=recognize), patch.object(knowledge_ocr, "delete_remote") as delete:
            output = extract(job["request"], factory)
            delete.assert_not_called()
        self.assertEqual(output["state"], "ready", output)
        self.assertTrue(finish(job, output, factory))
        draft = client.get(f"/api/v1/knowledge/items/{item['id']}", headers=headers[0]).json()
        self.assertEqual(draft["status"], "needs_review")
        self.assertEqual(client.post("/api/v1/knowledge/ask", headers=headers[0], json={"question": "合成健康"}).json()["citations"], [])
        confirmed = client.post(f"/api/v1/knowledge/items/{item['id']}/ocr/confirm", headers=headers[0],
            json={"revision": draft["revision"], "confirmation": "已核对"})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["method"], "doubao_ocr_reviewed")
        citations = client.post("/api/v1/knowledge/ask", headers=headers[0], json={"question": "合成健康"}).json()["citations"]
        self.assertEqual(citations[0]["locator"], "第 1 页（AI OCR）")
        self.assertEqual(len(requests), 1)
        with factory() as db:
            self.assertIsNone(db.scalar(select(KnowledgeRemoteCleanup)))


if __name__ == "__main__":
    unittest.main()
