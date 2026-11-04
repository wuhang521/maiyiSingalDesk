"""Packet/path guards and ASR admission. Real codec acceptance is run separately."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import wave

import pytest

from app import knowledge_silk as silk
from app import knowledge_asr
from app.config import get_settings
from test_knowledge import kb, collect


def encoded(*, tencent=True, count=3):
    return (b"\x02" if tencent else b"") + b"#!SILK_V3" + (b"\x01\x00x" * count) + (b"" if tencent else b"\xff\xff")


@pytest.mark.parametrize("tencent", [True, False])
def test_headers_and_packets(tmp_path, tencent):
    source = tmp_path / "renamed.amr"; source.write_bytes(encoded(tencent=tencent))
    assert silk.is_silk(source) and silk.validate_packets(source.read_bytes()) == 3
    source.write_bytes(b"#!AMR\n"); assert not silk.is_silk(source)
    assert not silk.is_silk(tmp_path / "missing")


@pytest.mark.parametrize("data", [b"", b"#!SILK_V3", encoded(count=2), b"#!SILK_V3\x00\x00", b"#!SILK_V3\xfe\xff",
    b"#!SILK_V3\x01", b"#!SILK_V3\x02\x00x", b"#!SILK_V3\x01\x14" + b"x" * 5121,
    encoded(tencent=False) + b"x", encoded() + b"\x00"])
def test_invalid_packets_rejected_before_native_import(data):
    with pytest.raises(ValueError): silk.validate_packets(data)


def test_packet_count_limit(monkeypatch):
    monkeypatch.setattr(silk, "MAX_SECONDS", 1)
    with pytest.raises(ValueError, match="Too many"): silk.validate_packets(encoded(count=51))


def test_readiness_rejects_namespace_only_or_disabled_decoder(monkeypatch):
    monkeypatch.setattr(get_settings(),"knowledge_silk_enabled",True)
    monkeypatch.setitem(sys.modules,"pilk",SimpleNamespace())
    assert not knowledge_asr.silk_configuration()
    monkeypatch.setitem(sys.modules,"pilk",SimpleNamespace(decode=lambda:None))
    assert knowledge_asr.silk_configuration()
    monkeypatch.setattr(get_settings(),"knowledge_silk_enabled",False)
    assert not knowledge_asr.silk_configuration()


def codec(monkeypatch, *, data=b"\x00\x01" * 2400, diagnostic=False):
    calls=[]
    def decode(source, output, pcm_rate):
        assert pcm_rate == 24000
        calls.append(Path(source).read_bytes())
        Path(output).write_bytes(data)
        if diagnostic: os.write(1, b"native decode error\n")
    monkeypatch.setitem(sys.modules, "pilk", SimpleNamespace(decode=decode))
    return calls


def test_wav_conversion_preserves_original_and_cleans_private_files(tmp_path, monkeypatch):
    calls=codec(monkeypatch); source=tmp_path / "voice.aud"; source.write_bytes(encoded())
    target=tmp_path / "voice.wav"; silk.decode(source,target)
    assert calls == [encoded()] and source.read_bytes() == encoded()
    with wave.open(str(target)) as audio:
        assert (audio.getnchannels(),audio.getsampwidth(),audio.getframerate(),audio.getnframes()) == (1,2,24000,2400)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["voice.aud","voice.wav"]
    with pytest.raises(ValueError, match="target"): silk.decode(source,target)
    assert target.stat().st_size == 4844


@pytest.mark.parametrize("data,diagnostic", [(b"",False),(b"x",False),(b"\0\0",True)])
def test_bad_native_output_is_not_published(tmp_path, monkeypatch, data, diagnostic):
    codec(monkeypatch,data=data,diagnostic=diagnostic)
    source=tmp_path / "voice.silk"; source.write_bytes(encoded()); target=tmp_path / "voice.wav"
    with pytest.raises(ValueError): silk.decode(source,target)
    assert not target.exists() and list(tmp_path.iterdir()) == [source]


def test_limits_and_unsafe_input_do_not_invoke_native(tmp_path, monkeypatch):
    calls=codec(monkeypatch); source=tmp_path / "voice.silk"; source.write_bytes(encoded()); target=tmp_path / "voice.wav"
    monkeypatch.setattr(silk,"MAX_INPUT",1)
    with pytest.raises(ValueError): silk.decode(source,target)
    assert calls == []
    monkeypatch.setattr(silk,"MAX_INPUT",1000)
    link=tmp_path / "link.silk"
    os.link(source,link)
    with pytest.raises(ValueError): silk.decode(source,target)
    assert calls == []


@pytest.mark.parametrize("filename", ["voice.silk","voice.slk","voice.aud","renamed.amr","renamed.wav"])
def test_missing_decoder_does_not_reserve_paid_quota(kb, monkeypatch, filename):
    client, users, factory, _ = kb
    monkeypatch.setattr(get_settings(),"knowledge_cloud_asr_enabled",True)
    monkeypatch.setattr(get_settings(),"knowledge_cloud_asr_api_key","synthetic-key")
    monkeypatch.setattr(knowledge_asr.shutil,"which",lambda _:"synthetic-executable")
    item=collect(client,users[0],title="SILK admission test")
    response=client.post(f"/api/v1/knowledge/items/{item['id']}/assets",params={"filename":filename,"revision":item["revision"]},
        headers={**users[0],"Content-Type":"application/octet-stream"},content=encoded())
    assert response.status_code == 200
    item=response.json(); asset=item["assets"][0]
    response=client.post(f"/api/v1/knowledge/items/{item['id']}/assets/{asset['id']}/asr",headers=users[0],json={"revision":item["revision"],"cloud_consent":True})
    assert response.status_code == 503 and "SILK" in response.json()["detail"]
    assert client.get("/api/v1/knowledge/usage",headers=users[0]).json()["daily_submissions"] == 0


def test_silk_is_detected_before_any_probe_or_cloud_submission(tmp_path, monkeypatch):
    source=tmp_path/"renamed.amr"; source.write_bytes(encoded())
    monkeypatch.setattr(knowledge_asr,"silk_configuration",lambda:False)
    def unexpected(*args,**kwargs): raise AssertionError("Probe must not run")
    monkeypatch.setattr(knowledge_asr,"local_command",unexpected)
    with pytest.raises(knowledge_asr.AsrError,match="SILK"):
        knowledge_asr.prepare_audio(source,"renamed.amr",tmp_path/"output.wav")
