"""Deferred sparse-frame cases; synthetic frames, no FFmpeg or paid requests."""
from pathlib import Path

import pytest

from app import knowledge_ocr, knowledge_video_ocr
from app.knowledge_extract import Extraction


def synthetic_frames(path, filename, directory):
    outputs = []
    for at in [0.0, 8.5]:
        frame = directory / f"synthetic-{at}.png"
        frame.write_bytes(b"synthetic-frame-not-real-media")
        outputs.append((at, frame))
    return outputs


def test_sparse_video_result_is_explicit_draft_with_time_locations(monkeypatch):
    monkeypatch.setattr(knowledge_video_ocr, "configuration", lambda: True)
    calls = []
    def recognize(path, filename, remember, *, allow_empty, may_send):
        calls.append(filename)
        assert filename == "frame.png" and allow_empty
        assert callable(may_send)
        return Extraction("合成画面文字", "doubao_ocr_draft", [])
    result = knowledge_video_ocr.recognize(Path("synthetic.mp4"), "private-original.mp4", lambda: True,
        frame_extractor=synthetic_frames, image_recognizer=recognize)
    assert result.method == "video_ocr_draft" and "非完整视频转录" in result.text and "未识别音轨" in result.text
    assert [locator for locator, _ in result.chunks] == ["0.00s 采样画面（AI OCR）", "8.50s 采样画面（AI OCR）"]
    assert calls == ["frame.png", "frame.png"]


def test_video_revocation_stops_remaining_frames_no_partial_admission(monkeypatch):
    monkeypatch.setattr(knowledge_video_ocr, "configuration", lambda: True)
    checks = iter([True, False]); calls = []
    def recognize(*args, **kwargs):
        calls.append(1); return Extraction("合成文字", "doubao_ocr_draft", [])
    with pytest.raises(knowledge_ocr.OcrError, match="已停止后续"):
        knowledge_video_ocr.recognize(Path("synthetic.mp4"), "synthetic.mp4", lambda: next(checks),
            frame_extractor=synthetic_frames, image_recognizer=recognize)
    assert len(calls) == 1


def test_video_empty_text_is_not_success(monkeypatch):
    monkeypatch.setattr(knowledge_video_ocr, "configuration", lambda: True)
    with pytest.raises(knowledge_ocr.OcrError, match="没有可识别文字"):
        knowledge_video_ocr.recognize(Path("synthetic.mp4"), "synthetic.mp4", lambda: True,
            frame_extractor=synthetic_frames, image_recognizer=lambda *args, **kwargs: Extraction("", "doubao_ocr_draft", []))
