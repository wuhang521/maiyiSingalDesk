"""Prepare health content early; deliver a frozen snapshot with durable ACKs.

Only this module's private SQLite ledger/state is written. The collector is a
runtime adapter: no private source is copied, and deliver never runs a crawler.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import httpx

SHANGHAI = ZoneInfo("Asia/Shanghai")
BODY_LIMIT = 3900
MAX_ATTEMPTS = 3
TEST_HEADING = re.compile(r"^(###[ \t]+\d+[.、][ \t]*)([^\r\n]+)", re.MULTILINE)
# ntfy's ordinary JSON/text publishing uses Go strings.TrimSpace. Keep the
# original packet/snapshot bytes, and use its precise Unicode White Space set
# only when comparing the provider's acknowledgement (not UnifiedPush).
NTFY_EDGE_WHITESPACE = ("\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
                        "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000")


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("State directory cannot be a symlink")
    path.chmod(0o700)


def atomic_json(path: Path, value: dict) -> None:
    private_dir(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def exclusive(path: Path):
    """A process lock spans HTTP calls and SQLite commits, including on Windows."""
    private_dir(path.parent)
    with path.open("a+b") as stream:
        os.chmod(path, 0o600)
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Another health worker owns this state") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def split_utf8(text: str, limit: int = BODY_LIMIT) -> list[str]:
    """Preserve every character, preferring newline boundaries below the limit."""
    if limit < 4:
        raise ValueError("UTF-8 limit must allow a full code point")
    result, current = [], ""
    for line in text.splitlines(keepends=True):
        if len((current + line).encode("utf-8")) <= limit:
            current += line
            continue
        if current and len(line.encode("utf-8")) <= limit:
            result.append(current)
            current = ""
        for character in line:
            if len((current + character).encode("utf-8")) > limit:
                result.append(current)
                current = ""
            current += character
    if current:
        result.append(current)
    return result


@dataclass(frozen=True)
class Config:
    server: str
    topic: str
    token: str = ""
    collector_python: str = ""
    max_items: int = 100
    start_date: date | None = None

    @classmethod
    def load(cls, path: Path) -> "Config":
        # This is an independent private file, never the collector's .env.
        from dotenv import dotenv_values
        metadata = path.stat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Private config must be a regular file")
        if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError("Private config must not be accessible to group or other users")
        value = dotenv_values(path, interpolate=False)
        server = str(value.get("NTFY_SERVER") or "").strip().rstrip("/")
        topic = str(value.get("HEALTH_NTFY_TOPIC") or "").strip()
        token = str(value.get("NTFY_TOKEN") or "").strip()
        parsed = urlsplit(server)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Invalid NTFY_SERVER")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", topic):
            raise ValueError("Invalid HEALTH_NTFY_TOPIC")
        if any(c in token for c in "\r\n\x00"):
            raise ValueError("Invalid NTFY_TOKEN")
        maximum = int(value.get("HEALTH_MAX_ITEMS") or "100")
        if not 1 <= maximum <= 100:
            raise ValueError("HEALTH_MAX_ITEMS must be between 1 and 100")
        start = value.get("HEALTH_START_DATE") or ""
        if start and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
            raise ValueError("HEALTH_START_DATE must be an ISO date")
        start_date = date.fromisoformat(start) if start else None
        return cls(server, topic, token, str(value.get("COLLECTOR_PYTHON") or ""), maximum, start_date)


# The program below executes only on the collector's host/runtime. It imports
# the public runtime contract, returns article data, and never reads source code.
COLLECTOR_ADAPTER = r'''
import json,sys
from pathlib import Path
from datetime import date
from zoneinfo import ZoneInfo
sys.path.insert(0,sys.argv[1])
from src.content_loader import load_content
from src.signaldesk_client import parse_articles
from src.main import split_ntfy_body
content=load_content(date.fromisoformat(sys.argv[2]),Path(sys.argv[3]))
if content is None: raise RuntimeError('No prepared health content')
print(json.dumps({'markdown':content.body,'articles':parse_articles('health',date.fromisoformat(sys.argv[2]),content.body,ZoneInfo('Asia/Shanghai')),'bodies':split_ntfy_body(content.body)},ensure_ascii=False))
'''


class Collector:
    def __init__(self, root: Path, config: Config):
        self.root, self.config = root.resolve(), config
        self.python = config.collector_python or str(self.root / ".venv/bin/python")

    @staticmethod
    def environment() -> dict[str, str]:
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE",
                   "LANG", "LANGUAGE", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
                   "CURL_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "TEMP", "TMP"}
        value = {key: content for key, content in os.environ.items()
                 if key.upper() in allowed or key.upper().startswith("LC_")}
        value.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
        return value

    def prepare(self, target_date: str, output_dir: Path) -> dict:
        private_dir(output_dir)
        # Task's current TLS bundle serves the fixed public RSS sources; keep
        # the legacy Python only for the existing Markdown/article contract.
        command = [sys.executable, str(Path(__file__).with_name("health_collector.py")),
                   "--collector-root", str(self.root), "--date", target_date,
                   "--max-items", str(self.config.max_items), "--output-dir", str(output_dir)]
        # Output may contain URLs or credentials on failures; do not echo it.
        process = subprocess.run(command, cwd=self.root, capture_output=True, timeout=120, env=self.environment())
        if process.returncode:
            raise RuntimeError("Health collector preparation failed")
        expected = output_dir / f"{target_date}.md"
        if not expected.is_file():
            raise RuntimeError("Health collector did not produce the target date")
        process = subprocess.run([self.python, "-c", COLLECTOR_ADAPTER, str(self.root), target_date,
                                  str(output_dir)], cwd=self.root,
                                 capture_output=True, timeout=120, encoding="utf-8", env=self.environment())
        if process.returncode:
            raise RuntimeError("Health collector parsing failed")
        return json.loads(process.stdout)


class Transport:
    def __init__(self, config: Config, client: httpx.Client | None = None):
        self.config = config
        self.client = client or httpx.Client(timeout=httpx.Timeout(25, connect=5), follow_redirects=False)

    def ingest(self, items: list[dict]) -> None:
        from .config import get_settings
        key = get_settings().api_key.strip()
        if not key or key == "change-me":
            raise RuntimeError("Task API key is not configured")
        response = self.client.post("http://127.0.0.1:19080/api/v1/articles/batch",
                                    headers={"X-SignalDesk-Key": key}, json={"items": items})
        response.raise_for_status()

    def headers(self) -> dict:
        return {"Authorization": "Bearer " + self.config.token} if self.config.token else {}

    def publish(self, title: str, body: str, tags: list[str] | None = None) -> dict:
        response = self.client.post(self.config.server + "/", headers=self.headers(),
                                    json={"topic": self.config.topic, "title": title, "message": body,
                                          "priority": 3, "tags": tags or ["green_book"]})
        response.raise_for_status()
        return response.json()

    def cached(self, since: int) -> list[dict]:
        response = self.client.get(self.config.server + "/" + quote(self.config.topic, safe="") + "/json",
                                   params={"poll": "1", "since": str(max(0, since))}, headers=self.headers())
        response.raise_for_status()
        # ntfy poll is finite NDJSON, not a streaming subscription.
        if len(response.content) > 16 * 1024 * 1024:
            raise ValueError("ntfy cache response too large")
        return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def clearly_rejected(error: Exception) -> bool:
    if isinstance(error, httpx.ConnectError):
        return True  # Connection was never established.
    return isinstance(error, httpx.HTTPStatusError) and 400 <= error.response.status_code < 500


class Worker:
    def __init__(self, state: Path, collector: Any, transport: Any, *, at: datetime | None = None,
                 test_run: bool = False, run_id: str = "", start_date: date | None = None):
        if at is not None and not test_run:
            raise ValueError("Clock override requires --test-run")
        if test_run and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
            raise ValueError("Test run requires a safe unique --run-id")
        if run_id and not test_run:
            raise ValueError("--run-id requires --test-run")
        current = at or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("Clock must include an explicit timezone")
        self.now = current.astimezone(SHANGHAI)
        self.day = self.now.date().isoformat()
        self.test_run, self.run_id = test_run, run_id
        self.start_date = start_date
        self.run_key = ("test:" + run_id + ":" if test_run else "health:") + self.day
        self.state = state.resolve()
        private_dir(self.state)
        self.collector, self.transport = collector, transport
        self.snapshot = self.state / "snapshots" / (self.run_key.replace(":", "_") + ".json")

    def connection(self) -> sqlite3.Connection:
        db_path = self.state / "ledger.sqlite3"
        db = sqlite3.connect(db_path, timeout=5)
        os.chmod(db_path, 0o600)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (run_key TEXT PRIMARY KEY, snapshot_sha TEXT NOT NULL, ingested INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS batches (run_key TEXT NOT NULL, batch_id TEXT NOT NULL,
                title TEXT NOT NULL, body TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, attempted_at INTEGER NOT NULL DEFAULT 0,
                ntfy_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (run_key, batch_id));
        """)
        return db

    def prepare(self) -> dict:
        if not self.test_run and self.start_date and self.now.date() < self.start_date:
            return {"state": "not_due", "run": self.run_key}
        with exclusive(self.state / "prepare.lock"):
            if self.snapshot.exists():
                value = self.load_snapshot()
                return {"state": "prepared", "run": self.run_key, "articles": len(value["articles"]), "reused": True}
            collected = self.state / "collected" / ("test-" + self.run_id if self.test_run else "health")
            value = self.collector.prepare(self.day, collected)
            markdown = value["markdown"]
            if not isinstance(markdown, str) or not markdown.strip():
                raise ValueError("Empty health snapshot")
            from .schemas import ArticleInput
            articles = []
            test_prefix = f"[测试 {self.run_id}] " if self.test_run else ""
            for original in value["articles"]:
                item = {**original, "channelId": "health"}
                if self.test_run:
                    item["id"] = "test:" + self.run_id + ":" + sha(item["id"])[:32]
                    item["title"] = test_prefix + item["title"]
                articles.append(ArticleInput.model_validate(item).model_dump())
            if not articles or len(articles) > 1000:
                raise ValueError("No valid health articles")
            bodies = value.get("bodies")
            if not isinstance(bodies, list) or not bodies or any(not isinstance(body, str) or not body.strip() for body in bodies):
                bodies = [markdown]
            if self.test_run:
                bodies = [TEST_HEADING.sub(lambda heading: heading[1] + test_prefix + heading[2], body) for body in bodies]
            bodies = [part for body in bodies for part in split_utf8(body)]
            snapshot = {"schema": 1, "run": self.run_key, "date": self.day,
                        "prepared_at": int(self.now.timestamp()), "markdown": markdown,
                        "articles": articles, "bodies": bodies}
            snapshot["sha256"] = sha(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
            atomic_json(self.snapshot, snapshot)
            return {"state": "prepared", "run": self.run_key, "articles": len(articles), "batches": len(bodies), "reused": False}

    def load_snapshot(self) -> dict:
        value = json.loads(self.snapshot.read_text(encoding="utf-8"))
        expected = value.pop("sha256")
        if value.get("run") != self.run_key or value.get("date") != self.day or value.get("schema") != 1:
            raise ValueError("Wrong health snapshot identity")
        if sha(json.dumps(value, ensure_ascii=False, sort_keys=True)) != expected:
            raise ValueError("Health snapshot checksum mismatch")
        value["sha256"] = expected
        return value

    def add_batch(self, db: sqlite3.Connection, identity: str, title: str, body: str) -> None:
        if len(body.encode("utf-8")) > BODY_LIMIT:
            raise ValueError("ntfy batch exceeds UTF-8 limit")
        fingerprint = sha(title + "\0" + body)
        db.execute("INSERT OR IGNORE INTO batches(run_key,batch_id,title,body,fingerprint,status) VALUES(?,?,?,?,?,'pending')",
                   (self.run_key, identity, title, body, fingerprint))
        row = db.execute("SELECT fingerprint FROM batches WHERE run_key=? AND batch_id=?", (self.run_key, identity)).fetchone()
        if row["fingerprint"] != fingerprint:
            raise ValueError("Immutable health batch changed")
        db.commit()

    @staticmethod
    def matches(message: dict, row: sqlite3.Row) -> bool:
        if message.get("event") != "message" or not isinstance(message.get("id"), str) or not message["id"]:
            return False
        title, body = str(message.get("title", "")), str(message.get("message", ""))
        if sha(title + "\0" + body) == row["fingerprint"]:
            return True
        return (title == row["title"] and body == row["body"].strip(NTFY_EDGE_WHITESPACE)
                and sha(row["title"] + "\0" + row["body"]) == row["fingerprint"])

    def reconcile(self, db: sqlite3.Connection, row: sqlite3.Row) -> bool:
        try:
            messages = self.transport.cached(row["attempted_at"] - 120)
            message = next((item for item in messages if self.matches(item, row)), None)
        except Exception:
            message = None
        if message:
            db.execute("UPDATE batches SET status='sent', ntfy_id=?, error='' WHERE run_key=? AND batch_id=?",
                       (message["id"], self.run_key, row["batch_id"]))
            db.commit()
            return True
        db.execute("UPDATE batches SET status='unknown', error='ack_unconfirmed' WHERE run_key=? AND batch_id=?",
                   (self.run_key, row["batch_id"]))
        db.commit()
        return False

    def send_batch(self, db: sqlite3.Connection, identity: str) -> str:
        while True:
            row = db.execute("SELECT * FROM batches WHERE run_key=? AND batch_id=?", (self.run_key, identity)).fetchone()
            if row["status"] == "sent":
                return "sent"
            if row["status"] in {"sending", "unknown"}:
                return "sent" if self.reconcile(db, row) else "unknown"
            if row["attempts"] >= MAX_ATTEMPTS:
                return "failed"
            db.execute("UPDATE batches SET status='sending', attempts=attempts+1, attempted_at=?, error='' WHERE run_key=? AND batch_id=?",
                       (int(self.now.timestamp()), self.run_key, identity))
            db.commit()  # A crash after dispatch leaves a durable uncertain state.
            try:
                ack = (self.transport.publish(row["title"], row["body"], tags=["health_status"])
                       if identity == "delay" else self.transport.publish(row["title"], row["body"]))
                if not self.matches(ack, row):
                    raise ValueError("Invalid ntfy acknowledgement")
            except Exception as error:
                if clearly_rejected(error):
                    db.execute("UPDATE batches SET status='failed', error='explicit_rejection' WHERE run_key=? AND batch_id=?",
                               (self.run_key, identity))
                    db.commit()
                    # One explicit rejection per invocation. The timer spaces
                    # retries by two minutes; the durable total budget is three.
                    return "failed"
                return "sent" if self.reconcile(db, db.execute("SELECT * FROM batches WHERE run_key=? AND batch_id=?", (self.run_key, identity)).fetchone()) else "unknown"
            db.execute("UPDATE batches SET status='sent', ntfy_id=?, error='' WHERE run_key=? AND batch_id=?",
                       (ack["id"], self.run_key, identity))
            db.commit()
            return "sent"

    def deliver(self) -> dict:
        if not self.test_run and self.start_date and self.now.date() < self.start_date:
            return {"state": "not_due", "run": self.run_key}
        if self.now.time() < time(9, 0):
            return {"state": "not_due", "run": self.run_key}
        with exclusive(self.state / "deliver.lock"), self.connection() as db:
            label = (f"[测试 {self.run_id}] " if self.test_run else "") + "健康管理商业情报 | " + self.day
            try:
                snapshot = self.load_snapshot()
            except (OSError, ValueError, KeyError, TypeError):
                body = f"{self.day} 健康资讯正在准备，今日内容将延迟送达。准备完成后会自动补送；本条为状态提示，不含文章。"
                self.add_batch(db, "delay", label + " · 延迟提示", body)
                result = self.send_batch(db, "delay")
                return {"state": "delayed" if result == "sent" else result, "run": self.run_key, "articles": 0}
            db.execute("INSERT OR IGNORE INTO runs(run_key,snapshot_sha) VALUES(?,?)", (self.run_key, snapshot["sha256"]))
            row = db.execute("SELECT * FROM runs WHERE run_key=?", (self.run_key,)).fetchone()
            if row["snapshot_sha"] != snapshot["sha256"]:
                raise ValueError("Delivered health snapshot is immutable")
            if not row["ingested"]:
                # Idempotent article IDs make retry after a lost API ACK safe.
                self.transport.ingest(snapshot["articles"])
                db.execute("UPDATE runs SET ingested=1 WHERE run_key=?", (self.run_key,))
                db.commit()
            states = []
            for index, body in enumerate(snapshot["bodies"], 1):
                identity = f"content-{index}-" + sha(body)[:16]
                title = label + f" [{index}/{len(snapshot['bodies'])}]"
                self.add_batch(db, identity, title, body)
                states.append(self.send_batch(db, identity))
            outcome = "sent" if all(state == "sent" for state in states) else ("unknown" if "unknown" in states else "failed")
            return {"state": outcome, "run": self.run_key, "articles": len(snapshot["articles"]), "batches": states}

    def status(self) -> dict:
        with self.connection() as db:
            rows = db.execute("SELECT batch_id,status,attempts,error FROM batches WHERE run_key=? ORDER BY batch_id", (self.run_key,)).fetchall()
            return {"run": self.run_key, "snapshot_present": self.snapshot.is_file(), "batches": [dict(row) for row in rows]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["prepare", "deliver", "status"], required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--collector-root", type=Path, default=Path("/opt/signaldesk"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--test-run", action="store_true")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--at", help="ISO-8601 clock, only with --test-run")
    args = parser.parse_args(argv)
    try:
        current = datetime.fromisoformat(args.at) if args.at else None
        config = Config.load(args.config) if args.config else None
        if args.mode != "status" and config is None:
            raise ValueError("--config is required")
        worker = Worker(args.state_dir, Collector(args.collector_root, config) if config else None,
                        Transport(config) if config else None, at=current, test_run=args.test_run, run_id=args.run_id,
                        start_date=config.start_date if config else None)
        result = getattr(worker, args.mode)()
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result.get("state") in {"failed", "unknown"} else 0
    except Exception as error:
        # Never print request URLs, private config, subprocess stderr or tokens.
        print(json.dumps({"state": "error", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
