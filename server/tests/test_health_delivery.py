"""Business acceptance of the independent scheduler; all HTTP is synthetic."""
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
from types import SimpleNamespace
import uuid

import httpx
import pytest

from app import health_delivery as service


DAY = "2026-10-30"
MARKDOWN = "# 健康资讯\n\n### 1. 原始健康标题\n" + "中文摘要和来源 https://example.test/health\n" * 8
ARTICLE = {"id": "daily:health:2026-10-30:1", "channelId": "demo-health", "title": "原始健康标题",
           "body": "原始正文", "timestamp": 1793318400000, "source": "公开样本", "clickUrl": "https://example.test/health", "tags": [], "priority": 3}


class Collector:
    def __init__(self, bodies=None):
        self.calls = []
        self.bodies = bodies or [MARKDOWN]

    def prepare(self, date, folder):
        self.calls.append((date, folder))
        return {"markdown": MARKDOWN, "articles": [dict(ARTICLE)], "bodies": list(self.bodies)}


class Transport:
    def __init__(self):
        self.ingested, self.published, self.cache, self.order, self.tags = [], [], [], [], []
        self.fail = None
        self.ingest_error = False
        self.accept_then_timeout = False
        self.hide_cache = False

    def ingest(self, articles):
        self.order.append("ingest")
        if self.ingest_error:
            raise httpx.ConnectError("synthetic")
        self.ingested.append(articles)

    def publish(self, title, body, tags=None):
        self.order.append("publish")
        self.tags.append(tags or ["green_book"])
        self.published.append((title, body))
        if self.fail:
            raise self.fail
        ack = {"event": "message", "id": str(len(self.published)), "title": title, "message": body}
        self.cache.append(ack)
        if self.accept_then_timeout:
            raise httpx.ReadTimeout("synthetic lost ACK")
        return ack

    def cached(self, since):
        self.order.append("cache")
        return [] if self.hide_cache else self.cache


def worker(tmp_path, collector=None, transport=None, at="2026-10-30T09:00:00+08:00", run="acceptance"):
    return service.Worker(tmp_path, collector or Collector(), transport or Transport(),
                          at=datetime.fromisoformat(at), test_run=True, run_id=run)


def test_0815_prepares_but_0859_cannot_send_and_0900_delivers(tmp_path):
    collector, transport = Collector(), Transport()
    early = worker(tmp_path, collector, transport, "2026-10-30T08:15:00+08:00")
    assert early.prepare()["state"] == "prepared"
    assert "### 1. 原始健康标题" in early.load_snapshot()["markdown"]
    assert early.load_snapshot()["articles"][0]["channelId"] == "health"
    assert collector.calls[0][1].name == "test-acceptance"
    assert worker(tmp_path, collector, transport, "2026-10-30T08:59:59+08:00").deliver()["state"] == "not_due"
    assert not transport.ingested and not transport.published
    collector.prepare = lambda *args: pytest.fail("09:00 must never collect")
    due = worker(tmp_path, collector, transport)
    assert due.deliver()["state"] == "sent"
    assert transport.order[:2] == ["ingest", "publish"]
    assert len(transport.published) == 1
    assert "[测试 acceptance]" in transport.published[0][0]
    assert "### 1. [测试 acceptance] 原始健康标题" in transport.published[0][1]
    assert due.deliver()["state"] == "sent"
    assert len(transport.published) == len(transport.ingested) == 1


def test_timezone_utc_rollover_and_clock_override_guard(tmp_path):
    assert worker(tmp_path, at="2026-10-29T17:00:00+00:00").day == DAY
    assert worker(tmp_path, at="2026-10-30T00:59:59+00:00").deliver()["state"] == "not_due"
    assert worker(tmp_path, at="2026-10-30T01:00:00+00:00").deliver()["state"] == "delayed"
    with pytest.raises(ValueError, match="override"):
        service.Worker(tmp_path, None, None, at=datetime.fromisoformat("2026-10-30T09:00:00+08:00"))
    with pytest.raises(ValueError, match="timezone"):
        worker(tmp_path, at="2026-10-30T09:00:00")
    with pytest.raises(ValueError, match="unique"):
        worker(tmp_path, run="../bad")
    with pytest.raises(ValueError, match="run-id"):
        service.Worker(tmp_path, None, None, run_id="unlabelled")


@pytest.mark.parametrize("text", ["中" * 3900, "😊\n" * 1700, "a\r\n" * 3000, "### 1. " + "中" * 1500, "a" * 3900])
def test_utf8_batch_limits_without_losing_a_character(text):
    result = service.split_utf8(text)
    assert "".join(result) == text
    assert result and all(0 < len(part.encode("utf-8")) <= 3900 for part in result)


def test_collector_split_preserved_and_oversized_chinese_split_stable(tmp_path):
    transport = Transport()
    w = worker(tmp_path, Collector(["### 1. 标题\n" + "中" * 2000, "### 2. 次标题\n正文"]), transport)
    w.prepare()
    snapshot = w.load_snapshot()
    assert "".join(snapshot["bodies"]).endswith("### 2. [测试 acceptance] 次标题\n正文")
    assert w.deliver()["state"] == "sent"
    before = list(transport.published)
    assert len(before) == 3 and len({title for title, _ in before}) == 3
    assert all(len(body.encode("utf-8")) <= 3900 for _, body in before)
    assert w.prepare()["reused"]
    assert w.deliver()["state"] == "sent" and transport.published == before


def test_no_snapshot_single_delay_then_prepared_supplement_once(tmp_path):
    transport = Transport()
    w = worker(tmp_path, transport=transport)
    assert w.deliver() == {"state": "delayed", "run": "test:acceptance:2026-10-30", "articles": 0}
    assert w.deliver()["state"] == "delayed"
    assert len(transport.published) == 1 and not transport.ingested
    assert "状态提示" in transport.published[0][1] and "###" not in transport.published[0][1]
    assert transport.tags == [["health_status"]]
    w.prepare()
    assert w.deliver()["state"] == "sent"
    assert w.deliver()["state"] == "sent"
    assert len(transport.published) == 2 and len(transport.ingested) == 1
    assert transport.tags == [["health_status"], ["green_book"]]


def test_ingest_failure_prevents_all_ntfy_dispatch_and_is_retryable(tmp_path):
    transport = Transport()
    w = worker(tmp_path, transport=transport)
    w.prepare()
    transport.ingest_error = True
    with pytest.raises(httpx.ConnectError):
        w.deliver()
    assert not transport.published
    transport.ingest_error = False
    assert w.deliver()["state"] == "sent"


def test_failed_preparation_never_creates_an_article_snapshot(tmp_path):
    collector, transport = Collector(), Transport()
    def unavailable(*args):
        raise RuntimeError("synthetic generator unavailable")
    collector.prepare = unavailable
    w = worker(tmp_path, collector, transport)
    with pytest.raises(RuntimeError):
        w.prepare()
    assert not w.snapshot.exists()
    assert w.deliver()["state"] == "delayed"
    assert len(transport.published) == 1 and not transport.ingested


def test_0900_delivery_does_not_wait_for_ongoing_collection(tmp_path):
    collector, transport = Collector(), Transport()
    entered, release = threading.Event(), threading.Event()
    normal = collector.prepare
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return normal(*args)
    collector.prepare = slow
    w = worker(tmp_path, collector, transport)
    errors = []
    def prepare():
        try:
            w.prepare()
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=prepare)
    thread.start()
    try:
        assert entered.wait(5)
        assert w.deliver()["state"] == "delayed"
        assert not transport.ingested
    finally:
        release.set()
        thread.join(5)
    assert not errors and not thread.is_alive()
    assert w.deliver()["state"] == "sent" and len(transport.published) == 2


def test_lost_ack_reconciled_from_exact_cache_body_and_title(tmp_path):
    transport = Transport()
    transport.accept_then_timeout = True
    w = worker(tmp_path, transport=transport)
    w.prepare()
    assert w.deliver()["state"] == "sent"
    assert w.deliver()["state"] == "sent"
    assert len(transport.published) == 1 and "cache" in transport.order


def test_unconfirmed_ack_never_blindly_retried_then_cache_recovers(tmp_path):
    transport = Transport()
    transport.accept_then_timeout, transport.hide_cache = True, True
    w = worker(tmp_path, transport=transport)
    w.prepare()
    assert w.deliver()["state"] == "unknown"
    transport.cache.append({**transport.cache[0], "title": "wrong title"})
    transport.hide_cache = False
    actual = transport.cache.pop(0)
    assert w.deliver()["state"] == "unknown" and len(transport.published) == 1
    transport.cache.append(actual)
    assert w.deliver()["state"] == "sent" and len(transport.published) == 1


def test_crash_after_send_before_ack_is_persisted_and_not_resent(tmp_path):
    class Crash(BaseException):
        pass
    transport = Transport()
    normal = transport.publish
    def interrupted(title, body):
        normal(title, body)
        raise Crash()
    transport.publish = interrupted
    w = worker(tmp_path, transport=transport)
    w.prepare()
    with pytest.raises(Crash):
        w.deliver()
    assert w.status()["batches"][0]["status"] == "sending"
    transport.publish = normal
    assert w.deliver()["state"] == "sent" and len(transport.published) == 1


def test_explicit_rejections_have_total_bounded_retry_budget(tmp_path):
    transport = Transport()
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.test"))
    transport.fail = httpx.HTTPStatusError("synthetic rejection", request=response.request, response=response)
    w = worker(tmp_path, transport=transport)
    w.prepare()
    assert w.deliver()["state"] == "failed"
    assert len(transport.published) == 1
    assert w.deliver()["state"] == "failed"
    assert w.deliver()["state"] == "failed"
    assert len(transport.published) == 3
    transport.fail = None
    assert w.deliver()["state"] == "failed" and len(transport.published) == 3


def test_one_explicit_failure_then_success(tmp_path):
    transport = Transport()
    normal = transport.publish
    def once(title, body):
        transport.publish = normal
        raise httpx.ConnectError("connection not established")
    transport.publish = once
    w = worker(tmp_path, transport=transport)
    w.prepare()
    assert w.deliver()["state"] == "failed"
    assert w.deliver()["state"] == "sent"
    assert w.status()["batches"][0]["attempts"] == 2


def test_server_500_is_uncertain_not_a_safe_retry(tmp_path):
    transport = Transport()
    response = httpx.Response(500, request=httpx.Request("POST", "https://example.test"))
    transport.fail = httpx.HTTPStatusError("synthetic", request=response.request, response=response)
    w = worker(tmp_path, transport=transport)
    w.prepare()
    assert w.deliver()["state"] == "unknown"
    assert w.deliver()["state"] == "unknown" and len(transport.published) == 1


def test_partial_batch_rerun_skips_sent_batch_and_only_reconciles_unknown(tmp_path):
    transport = Transport()
    normal = transport.publish
    def partial(title, body):
        if "第二" in body:
            transport.published.append((title, body))
            raise httpx.ReadTimeout("synthetic uncertain second batch")
        return normal(title, body)
    transport.publish = partial
    w = worker(tmp_path, Collector(["### 1. 第一\n正文", "### 2. 第二\n正文"]), transport)
    w.prepare()
    assert w.deliver()["state"] == "unknown"
    assert len(transport.published) == 2
    transport.publish = lambda *args: pytest.fail("Neither sent nor uncertain batches may resend")
    transport.cached = lambda *args: (_ for _ in ()).throw(httpx.ReadTimeout("cache unavailable"))
    assert w.deliver()["state"] == "unknown" and len(transport.published) == 2
    assert sorted(row["status"] for row in w.status()["batches"]) == ["sent", "unknown"]


def test_concurrent_deliver_cannot_send_twice(tmp_path):
    transport = Transport()
    entered, release = threading.Event(), threading.Event()
    normal = transport.publish
    def slow(title, body):
        entered.set()
        assert release.wait(5)
        return normal(title, body)
    transport.publish = slow
    w = worker(tmp_path, transport=transport)
    w.prepare()
    errors = []
    def first():
        try:
            assert w.deliver()["state"] == "sent"
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(RuntimeError, match="owns"):
            worker(tmp_path, transport=transport).deliver()
    finally:
        release.set()
        thread.join(5)
    assert not errors and not thread.is_alive() and len(transport.published) == 1


def test_test_run_never_occupies_production_or_other_test_date(tmp_path):
    first, second = worker(tmp_path, run="one"), worker(tmp_path, run="two")
    first.prepare()
    first.deliver()
    assert not second.snapshot.exists() and second.status()["batches"] == []
    with sqlite3.connect(tmp_path / "ledger.sqlite3") as db:
        keys = [row[0] for row in db.execute("SELECT DISTINCT run_key FROM batches")]
    assert keys == ["test:one:2026-10-30"]
    assert first.load_snapshot()["articles"][0]["id"].startswith("test:one:")
    assert first.load_snapshot()["articles"][0]["title"].startswith("[测试 one]")


def test_corrupt_snapshot_sends_only_delay_and_is_not_overwritten(tmp_path):
    transport = Transport()
    w = worker(tmp_path, transport=transport)
    w.prepare()
    value = json.loads(w.snapshot.read_text(encoding="utf-8"))
    value["markdown"] = "corrupt"
    w.snapshot.write_text(json.dumps(value), encoding="utf-8")
    assert w.deliver()["state"] == "delayed" and not transport.ingested
    with pytest.raises(ValueError, match="checksum"):
        w.prepare()


def test_transport_http_contract_key_and_token_kept_in_headers(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(api_key="synthetic-task-secret"))
    seen = []
    def handler(request):
        seen.append(request)
        if "articles/batch" in request.url.path:
            assert request.url.host == "127.0.0.1" and request.url.port == 19080
            assert request.headers["X-SignalDesk-Key"] == "synthetic-task-secret"
            return httpx.Response(200, json={"created": 1})
        assert request.headers["Authorization"] == "Bearer synthetic-ntfy-secret"
        if request.method == "GET":
            return httpx.Response(200, text='{"event":"message","id":"ok","title":"标题","message":"正文"}\n')
        assert json.loads(request.content)["message"] == "正文"
        return httpx.Response(200, json={"event": "message", "id": "ok", "title": "标题", "message": "正文"})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = service.Transport(service.Config("https://ntfy.example.test", "health-private", "synthetic-ntfy-secret"), client)
    transport.ingest([ARTICLE])
    assert transport.publish("标题", "正文")["id"] == "ok"
    assert json.loads(seen[-1].content)["tags"] == ["green_book"]
    assert transport.publish("标题", "正文", tags=["health_status"])["id"] == "ok"
    assert json.loads(seen[-1].content)["tags"] == ["health_status"]
    assert transport.cached(100)[0]["id"] == "ok"
    assert "poll=1" in str(seen[-1].url)
    assert all("secret" not in str(request.url) for request in seen)


def test_config_and_cli_errors_never_reveal_secrets(tmp_path, capsys):
    private = tmp_path / "private.env"
    private.write_text("NTFY_SERVER=https://ntfy.example.test\nHEALTH_NTFY_TOPIC=health-private\nNTFY_TOKEN=synthetic-private-secret\n", encoding="utf-8")
    private.chmod(0o600)
    config = service.Config.load(private)
    assert config.topic == "health-private" and config.token == "synthetic-private-secret"
    assert config.max_items == 100
    assert service.main(["--mode", "deliver", "--state-dir", str(tmp_path), "--config", str(private), "--at", "2026-10-30T09:00:00+08:00"]) == 1
    assert "secret" not in capsys.readouterr().out


def test_generator_cli_uses_private_output_and_runtime_contract(tmp_path, monkeypatch):
    invocations = []
    output = tmp_path / "collected/health"
    def run(command, **kwargs):
        invocations.append((command, kwargs))
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        assert "TASK_API_KEY" not in kwargs["env"] and "DASHSCOPE_API_KEY" not in kwargs["env"]
        if command[1] != "-c":
            (output / f"{DAY}.md").write_text(MARKDOWN, encoding="utf-8")
            return SimpleNamespace(returncode=0)
        assert "parse_articles('health'" in command[2]
        assert "SimpleNamespace" not in command[2]
        assert "read_text" not in command[2] and "inspect" not in command[2]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"markdown": MARKDOWN, "articles": [ARTICLE], "bodies": [MARKDOWN]}))
    monkeypatch.setattr(service.subprocess, "run", run)
    monkeypatch.setenv("TASK_API_KEY", "synthetic-task-secret")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-model-secret")
    collector = service.Collector(tmp_path / "private-collector", service.Config("https://ntfy.example.test", "health-private", collector_python="/collector/python"))
    assert collector.prepare(DAY, output)["markdown"] == MARKDOWN
    assert invocations[0][0][:2] == [service.sys.executable, str(Path(service.__file__).with_name("health_collector.py"))]
    assert invocations[0][0][invocations[0][0].index("--collector-root") + 1] == str(collector.root)
    assert invocations[0][1]["timeout"] == 120
    assert invocations[1][0][0] == "/collector/python"
    assert invocations[1][0][3:] == [str(collector.root), DAY, str(output)]
    assert invocations[0][0][-2:] == ["--output-dir", str(output)]
    assert invocations[0][0][invocations[0][0].index("--max-items") + 1] == "100"
    assert all(invocation[1]["capture_output"] for invocation in invocations)


def test_timers_have_explicit_shanghai_deadlines_and_persistence():
    deploy = Path(__file__).parents[1] / "deploy"
    for mode, clock in [("prepare", "08:15:00"), ("deliver", "09:00:00")]:
        timer = (deploy / f"signaldesk-health-{mode}.timer").read_text()
        assert f"OnCalendar=*-*-* {clock}\n" in timer and "Persistent=true" in timer and "AccuracySec=1s" in timer
        assert "requires server timezone: Asia/Shanghai" in timer and "RandomizedDelaySec=" not in timer
        unit = (deploy / f"signaldesk-health-{mode}.service").read_text()
        assert f"--mode {mode}" in unit and "UMask=0077" in unit
    assert "OnUnitInactiveSec=2min" in (deploy / "signaldesk-health-deliver.timer").read_text()
    prepare = (deploy / "signaldesk-health-prepare.service").read_text()
    assert "Type=simple" in prepare and "StartLimitInterval=1h" in prepare and "StartLimitIntervalSec=" not in prepare


def test_private_parser_recipient_survives_real_json_ipc_and_article_validation(tmp_path, monkeypatch):
    from app.schemas import ArticleInput

    package = tmp_path / "private-runtime" / "src"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "content_loader.py").write_text(
        "from pathlib import Path\nfrom types import SimpleNamespace\n"
        "def load_content(day, directory):\n"
        " return SimpleNamespace(body=(Path(directory)/(day.isoformat()+'.md')).read_text(encoding='utf-8'))\n",
        encoding="utf-8")
    (package / "signaldesk_client.py").write_text(
        "def parse_articles(recipient:str,target_date,markdown,timezone):\n"
        f" article={ARTICLE!r}\n article['channelId']=recipient\n return [article]\n", encoding="utf-8")
    (package / "main.py").write_text("def split_ntfy_body(body):\n return [body]\n", encoding="utf-8")
    output = tmp_path / "output"
    real_run = service.subprocess.run

    def network_only_stub(command, **kwargs):
        if Path(command[1]).name == "health_collector.py":
            (output / f"{DAY}.md").write_text(MARKDOWN, encoding="utf-8")
            return SimpleNamespace(returncode=0)
        return real_run(command, **kwargs)

    monkeypatch.setattr(service.subprocess, "run", network_only_stub)
    config = service.Config("https://ntfy.example.test", "synthetic-private-topic",
                            collector_python=service.sys.executable)
    result = service.Collector(package.parent, config).prepare(DAY, output)
    article = ArticleInput.model_validate(result["articles"][0])
    assert article.channelId == "health"
    assert result["markdown"] == MARKDOWN and result["bodies"] == [MARKDOWN]


@pytest.mark.parametrize("padding", ["\n", "\r\n", "\t \u00a0\u2003"])
def test_real_ntfy_edge_trim_ack_keeps_snapshot_packet_and_fingerprint(tmp_path, padding):
    class TrimmingTransport(Transport):
        def publish(self, title, body, tags=None):
            acknowledgement = super().publish(title, body, tags)
            acknowledgement["message"] = body.strip()
            return acknowledgement

    transport = TrimmingTransport()
    w = worker(tmp_path, Collector([padding + "正文内部\n  保留缩进和换行" + padding]), transport)
    assert w.prepare()["state"] == "prepared"
    frozen = w.snapshot.read_bytes()
    assert w.deliver()["state"] == "sent"
    with w.connection() as db:
        row = db.execute("SELECT * FROM batches").fetchone()
        assert row["body"] == transport.published[0][1]
        assert row["fingerprint"] == service.sha(row["title"] + "\0" + row["body"])
        assert row["attempts"] == 1 and row["ntfy_id"]
    assert w.snapshot.read_bytes() == frozen
    assert w.deliver()["state"] == "sent" and len(transport.published) == 1


@pytest.mark.parametrize("change", ["title", "internal", "id", "event", "fingerprint", "non_go_control"])
def test_trim_ack_never_accepts_other_content_or_corrupt_fingerprint(change):
    row = {"title": "固定标题", "body": "\n正文\n  内部缩进\n"}
    if change == "non_go_control":
        row["body"] = "\x1c正文\x1f"
    row["fingerprint"] = service.sha(row["title"] + "\0" + row["body"])
    message = {"id": "synthetic-ack", "event": "message", "title": row["title"], "message": row["body"].strip()}
    if change == "title": message["title"] += "不同"
    if change == "internal": message["message"] = message["message"].replace("\n  ", " ")
    if change == "id": message["id"] = ""
    if change == "event": message["event"] = "keepalive"
    if change == "fingerprint": row["fingerprint"] = "0" * 64
    assert not service.Worker.matches(message, row)


@pytest.mark.parametrize("maximum", ["0", "101", "not-a-number"])
def test_channel_maximum_must_be_a_bounded_integer(tmp_path, maximum):
    config = tmp_path / "private.env"
    config.write_text("NTFY_SERVER=https://ntfy.example.test\nHEALTH_NTFY_TOPIC=health-private\nHEALTH_MAX_ITEMS=" + maximum, encoding="utf-8")
    config.chmod(0o600)
    with pytest.raises(ValueError):
        service.Config.load(config)


@pytest.mark.skipif(os.name == "nt", reason="Windows requires ACL verification; POSIX mode is not a Windows ACL")
def test_posix_private_config_rejects_group_or_world_read(tmp_path):
    config = tmp_path / "private.env"
    config.write_text("NTFY_SERVER=https://ntfy.example.test\nHEALTH_NTFY_TOPIC=health-private\n", encoding="utf-8")
    config.chmod(0o640)
    with pytest.raises(ValueError, match="group"):
        service.Config.load(config)
    config.chmod(0o600)
    assert service.Config.load(config).topic == "health-private"


@pytest.mark.parametrize("cutover", ["20261031", "2026-02-30", "2026-10-31T09:00:00+08:00", "tomorrow"])
def test_cutover_config_requires_a_real_strict_iso_date(tmp_path, cutover):
    config = tmp_path / "private.env"
    config.write_text("NTFY_SERVER=https://ntfy.example.test\nHEALTH_NTFY_TOPIC=health-private\nHEALTH_START_DATE=" + cutover, encoding="utf-8")
    config.chmod(0o600)
    with pytest.raises(ValueError):
        service.Config.load(config)


@pytest.mark.parametrize("current,expected", [("2026-10-30T10:00:00+08:00", "not_due"), ("2026-10-31T09:00:00+08:00", "sent")])
def test_production_cutover_blocks_all_dispatch_until_start_date(tmp_path, monkeypatch, capsys, current, expected):
    instant = datetime.fromisoformat(current)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)
    monkeypatch.setattr(service, "datetime", Clock)
    collector, transport = Collector(), Transport()
    w = service.Worker(tmp_path, collector, transport, start_date=date(2026, 10, 31))
    prepared = w.prepare()
    assert w.deliver()["state"] == expected
    if expected == "not_due":
        assert prepared["state"] == "not_due"
        assert not collector.calls and not transport.order
        assert not w.snapshot.exists() and not (tmp_path / "ledger.sqlite3").exists()
        assert not (tmp_path / "prepare.lock").exists() and not (tmp_path / "deliver.lock").exists()
        config = tmp_path / "private.env"
        config.write_text("NTFY_SERVER=https://ntfy.example.test\nHEALTH_NTFY_TOPIC=health-private\nHEALTH_START_DATE=2026-10-31\n", encoding="utf-8")
        config.chmod(0o600)
        assert service.Config.load(config).start_date == date(2026, 10, 31)
        monkeypatch.setattr(service, "Collector", lambda *args: collector)
        monkeypatch.setattr(service, "Transport", lambda *args: transport)
        for mode in ("prepare", "deliver"):
            assert service.main(["--mode", mode, "--state-dir", str(tmp_path), "--config", str(config)]) == 0
            assert json.loads(capsys.readouterr().out)["state"] == "not_due"
        assert not collector.calls and not transport.order
    else:
        assert prepared["state"] == "prepared"
        assert len(collector.calls) == len(transport.ingested) == len(transport.published) == 1
        assert not transport.published[0][0].startswith("[测试")


def test_test_run_ignores_production_cutover_but_keeps_unique_run_guard(tmp_path):
    collector, transport = Collector(), Transport()
    w = service.Worker(tmp_path, collector, transport, at=datetime.fromisoformat("2026-10-30T09:00:00+08:00"),
                       test_run=True, run_id="cutover-acceptance", start_date=date(2026, 10, 31))
    assert w.prepare()["state"] == "prepared" and w.deliver()["state"] == "sent"
    assert transport.published[0][0].startswith("[测试 cutover-acceptance]")
    with pytest.raises(ValueError, match="unique"):
        service.Worker(tmp_path, collector, transport, test_run=True, start_date=date(2026, 10, 31))


@pytest.mark.parametrize("maximum", [1, 7, 100])
def test_public_collector_uses_task_runtime_and_preserves_the_configured_upper_bound(tmp_path, monkeypatch, maximum):
    output = tmp_path / "generated"
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if command[1] != "-c":
            (output / f"{DAY}.md").write_text(MARKDOWN, encoding="utf-8")
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"markdown": MARKDOWN, "articles": [ARTICLE], "bodies": [MARKDOWN]}))
    monkeypatch.setattr(service.subprocess, "run", run)
    config = service.Config("https://ntfy.example.test", "synthetic-health", max_items=maximum)
    result = service.Collector(tmp_path, config).prepare(DAY, output)
    command = commands[0]
    assert command[:2] == [service.sys.executable, str(Path(service.__file__).with_name("health_collector.py"))]
    assert command[command.index("--collector-root") + 1] == str(tmp_path.resolve())
    assert command[command.index("--max-items") + 1] == str(maximum)
    assert "src.health_news_generator" not in command
    assert "--min-items" not in command
    assert result["articles"] == [ARTICLE]


def golden_digest(ending):
    bodies, articles = [], []
    for index in (1, 2):
        title, source = f"中文标题{index}🙂", f"合成来源{index}"
        detail = f"业务摘要{index} " + "中" * 900 + "🙂"
        click = f"https://example.test/golden-{index}"
        bodies.append(f"### {index}. {title}{ending}来源：{source}{ending}链接：[打开原文]({click}){ending}主要内容：{detail}{ending}")
        articles.append({**ARTICLE, "id": f"daily:health:{DAY}:{index}", "title": title, "source": source,
                         "body": f"**来源：** {source}\n\n### 主要内容\n\n{detail}", "clickUrl": click})
    return {"markdown": "# 合成健康摘要" + ending + ending + "".join(bodies), "bodies": bodies, "articles": articles}


NODE = os.environ.get("HEALTH_CONTENT_CONTRACT_NODE") or shutil.which("node")


@pytest.mark.skipif(not NODE, reason="Actual bundled client-parser contract requires a local Node runtime")
@pytest.mark.parametrize("test_run,ending", [(False, "\n"), (False, "\r\n"), (True, "\n"), (True, "\r\n")])
def test_actual_client_parser_matches_marked_canonical_fields_across_chinese_crlf_packets(tmp_path, monkeypatch, test_run, ending):
    fixture = golden_digest(ending)
    class Prepared:
        def prepare(self, *args):
            return fixture
    instant = datetime.fromisoformat("2026-10-30T09:00:00+08:00")
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)
    monkeypatch.setattr(service, "datetime", Clock)
    run_id = "snapshot-label-" + uuid.uuid4().hex[:16]
    w = service.Worker(tmp_path, Prepared(), Transport(), at=instant if test_run else None,
                       test_run=test_run, run_id=run_id if test_run else "")
    assert w.prepare()["state"] == "prepared"
    snapshot = w.load_snapshot()
    assert snapshot["markdown"] == fixture["markdown"]
    assert len(snapshot["bodies"]) == 2 and all(len(body.encode("utf-8")) <= 3900 for body in snapshot["bodies"])
    checker = Path(__file__).parents[2] / "scripts/health-content-contract.cjs"
    process = subprocess.run([NODE, str(checker)], input=json.dumps({
        "channel": {"id": "health", "name": "健康管理商业情报", "server": "https://ntfy.example.test"},
        "bodies": snapshot["bodies"], "time": snapshot["prepared_at"], "title": "合成通知标题",
    }), text=True, encoding="utf-8", capture_output=True, timeout=15)
    assert process.returncode == 0
    actual = json.loads(process.stdout)
    assert actual["item_count"] == len(snapshot["articles"]) == 2
    differences = []
    for index, (item, parsed) in enumerate(zip(snapshot["articles"], actual["items"]), 1):
        assert parsed["heading_number"] == index
        for field in ("title", "body", "source", "clickUrl"):
            if parsed[field + "_sha256"] != service.sha(item.get(field) or ""):
                differences.append({"article": index, "field": field})
    assert differences == []
    prefix = f"[测试 {run_id}] "
    if test_run:
        assert all(item["title"].startswith(prefix) for item in snapshot["articles"])
        assert all(body.startswith(f"### {index}. " + prefix) for index, body in enumerate(snapshot["bodies"], 1))
    else:
        assert snapshot["bodies"] == fixture["bodies"]
        assert snapshot["articles"] == [{**item, "channelId": "health"} for item in fixture["articles"]]


@pytest.mark.parametrize("body_source", ["provided", "fallback"])
def test_test_heading_labels_precede_utf8_splitting_without_mutating_original_markdown(tmp_path, body_source):
    run_id = "label-byte-boundary-" + uuid.uuid4().hex[:12]
    original = "### 1. 第一中文标题🙂\r\n" + "中" * 1800 + "\r\n### 2. 第二中文标题\r\n正文🙂\r\n"
    class Prepared:
        def prepare(self, *args):
            return {"markdown": original, "bodies": [original] if body_source == "provided" else [], "articles": [
                {**ARTICLE, "title": "第一中文标题🙂", "body": "中" * 1800, "source": "ntfy.example.test", "clickUrl": None},
                {**ARTICLE, "id": "daily:health:2026-10-30:2", "title": "第二中文标题", "body": "正文🙂", "source": "ntfy.example.test", "clickUrl": None},
            ]}
    w = worker(tmp_path, collector=Prepared(), run=run_id)
    w.prepare(); snapshot = w.load_snapshot()
    expected = re.sub(r"(?m)^(###[ \t]+\d+[.、][ \t]*)([^\r\n]+)", lambda match: match[1] + f"[测试 {run_id}] " + match[2], original)
    assert snapshot["markdown"] == original
    assert "".join(snapshot["bodies"]) == expected
    assert len(snapshot["bodies"]) > 1
    assert all(0 < len(body.encode("utf-8")) <= 3900 for body in snapshot["bodies"])


@pytest.mark.parametrize("empty", ["articles", "markdown"])
def test_empty_generator_result_never_invents_or_publishes_an_article(tmp_path, empty):
    fixture = {"markdown": MARKDOWN, "articles": [dict(ARTICLE)], "bodies": [MARKDOWN]}
    fixture[empty] = [] if empty == "articles" else ""
    class Empty:
        def prepare(self, *args):
            return fixture
    transport = Transport(); w = worker(tmp_path, collector=Empty(), transport=transport, run="empty-result-" + uuid.uuid4().hex[:12])
    with pytest.raises(ValueError, match="Empty|No valid"):
        w.prepare()
    assert not w.snapshot.exists() and not transport.ingested and not transport.published
    assert w.deliver()["state"] == "delayed"
    assert not transport.ingested and all("###" not in body for _, body in transport.published)
