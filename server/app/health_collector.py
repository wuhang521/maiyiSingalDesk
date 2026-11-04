"""Bounded public RSS transport for the existing private health business policy.

Run with the Task Python runtime. This module never invokes the legacy network
collector, reads configuration, connects to a database or publishes a message.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import sys
import tempfile
import threading
import time
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    url: str


SOURCES = (
    Source("prnewswire_health", "PR Newswire（企业新闻稿）",
           "https://www.prnewswire.com/rss/health-latest-news/health-latest-news-list.rss"),
    Source("healthcare_dive", "Healthcare Dive", "https://www.healthcaredive.com/feeds/news/"),
)
MAX_PROVIDER_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_PROVIDER_ITEMS = 200
NETWORK_BUDGET_SECONDS = 25.0
REQUEST_TIMEOUT = httpx.Timeout(5.0, connect=5.0)
HEADERS = {"User-Agent": "SignalDesk-Health-RSS/0.6.4",
           "Accept": "application/rss+xml, application/xml, text/xml",
           "Accept-Encoding": "identity"}


@dataclass(frozen=True)
class CollectionResult:
    markdown: str
    summary: dict[str, Any]


class CollectorFailure(Exception):
    def __init__(self, code: str, summary: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.summary = summary or {"status": "failed", "error": code}


class NetworkBudgetExpired(TimeoutError):
    pass


@contextmanager
def _network_deadline(seconds: float):
    enabled = hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread()
    if not enabled:
        yield
        return
    started = time.monotonic()
    previous_handler = signal.getsignal(signal.SIGALRM)

    def expired(signum, frame):
        raise NetworkBudgetExpired()

    signal.signal(signal.SIGALRM, expired)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL,
                             max(0.001, previous_timer[0] - (time.monotonic() - started)), previous_timer[1])


def _has_tls_error(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _fetch(client: httpx.Client) -> tuple[list[tuple[Source, bytes]], list[dict[str, Any]]]:
    bodies: list[tuple[Source, bytes]] = []
    providers: list[dict[str, Any]] = []
    deadline = time.monotonic() + NETWORK_BUDGET_SECONDS
    total = 0
    with _network_deadline(NETWORK_BUDGET_SECONDS):
        for source in SOURCES:
            provider: dict[str, Any] = {"provider": source.id, "status": "failed", "response_bytes": 0}
            providers.append(provider)
            if time.monotonic() >= deadline:
                provider["error"] = "network_budget"
                continue
            response = None
            try:
                # A fresh Request avoids client default auth, cookies, headers
                # and any credentials an injected client might otherwise add.
                request = httpx.Request("GET", source.url, headers=HEADERS,
                                        extensions={"timeout": REQUEST_TIMEOUT.as_dict()})
                response = client.send(request, stream=True, auth=None, follow_redirects=False)
                provider["http_status"] = response.status_code
                if 300 <= response.status_code < 400:
                    provider["error"] = "redirect"
                    continue
                if response.status_code != 200:
                    provider["error"] = "http_status"
                    continue
                if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
                    provider["error"] = "content_encoding"
                    continue
                length = response.headers.get("content-length", "")
                if length.isdecimal() and int(length) > MAX_PROVIDER_BYTES:
                    provider["error"] = "response_size"
                    continue
                chunks: list[bytes] = []
                count = 0
                for block in response.iter_bytes(chunk_size=8192):
                    if time.monotonic() >= deadline:
                        raise NetworkBudgetExpired()
                    count += len(block)
                    total += len(block)
                    provider["response_bytes"] = count
                    if count > MAX_PROVIDER_BYTES or total > MAX_TOTAL_BYTES:
                        raise CollectorFailure("response_size")
                    chunks.append(block)
                bodies.append((source, b"".join(chunks)))
                provider["status"] = "fetched"
            except CollectorFailure as error:
                provider["error"] = error.code
            except (httpx.TimeoutException, TimeoutError):
                provider["error"] = "timeout"
            except httpx.TransportError as error:
                provider["error"] = "tls" if _has_tls_error(error) else "transport"
            except Exception:
                provider["error"] = "transport"
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        provider.update(status="failed", error="transport_close")
    return bodies, providers


def _public_https_link(link: str) -> bool:
    try:
        parsed = urlsplit(link)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or any(ord(char) < 33 for char in link)):
            return False
        hostname = parsed.hostname.removesuffix(".").lower()
        if "%" in hostname or hostname == "localhost" or hostname.endswith((".localhost", ".local")):
            return False
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            labels = hostname.encode("idna").decode("ascii").split(".")
            ascii_host = ".".join(labels)
            if (ascii_host == "localhost" or ascii_host.endswith((".localhost", ".local"))
                    or len(labels) < 2 or len(ascii_host) > 253 or any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)):
                return False
            # Reject noncanonical numeric/hex IP spellings browsers may treat
            # as a local address, without resolving ordinary DNS names.
            return not all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label) for label in labels)
        mapped = getattr(address, "ipv4_mapped", None)
        return (address.is_global and not address.is_multicast and not address.is_unspecified
                and (mapped is None or (mapped.is_global and not mapped.is_multicast)))
    except (ValueError, UnicodeError):
        return False


def _normalize(source: Source, body: bytes, now: datetime) -> tuple[list[dict[str, str]], dict[str, int]]:
    import xml.etree.ElementTree as ET

    # The fixed publishers use UTF-8. Reject NUL-bearing UTF-16/32 before
    # Expat can decode and expand a declaration hidden from a byte scan.
    if b"\x00" in body or b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise CollectorFailure("xml_declaration")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise CollectorFailure("bad_xml") from None
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise CollectorFailure("not_rss")
    nodes = channel.findall("item")
    if len(nodes) > MAX_PROVIDER_ITEMS:
        raise CollectorFailure("item_limit")
    counts = {"rss_items": len(nodes), "normalized": 0, "missing_fields": 0,
              "invalid_link": 0, "invalid_date": 0, "future_date": 0, "older_than_72h": 0}
    result = []
    for node in nodes:
        title = " ".join((node.findtext("title") or "").split())
        link = (node.findtext("link") or "").strip()
        published = (node.findtext("pubDate") or "").strip()
        if not title or not link or not published or len(title) > 1000 or len(link) > 4096 or len(published) > 200:
            counts["missing_fields"] += 1
            continue
        if not _public_https_link(link):
            counts["invalid_link"] += 1
            continue
        try:
            timestamp = parsedate_to_datetime(published)
            if timestamp.utcoffset() is None:
                raise ValueError()
            timestamp = timestamp.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            counts["invalid_date"] += 1
            continue
        if timestamp > now:
            counts["future_date"] += 1
            continue
        if timestamp < now - timedelta(hours=72):
            counts["older_than_72h"] += 1
            continue
        result.append({"title": title, "source": source.label, "link": link,
                       "published": published, "query": ""})
    counts["normalized"] = len(result)
    return result, counts


def _load_business(collector_root: Path):
    root = Path(collector_root)
    if root.is_symlink() or not root.is_dir():
        raise CollectorFailure("business_contract")
    source = root / "src" / "health_news_generator.py"
    try:
        info = source.lstat()
        if source.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise CollectorFailure("business_contract")
        raw = source.read_bytes()
        tree = ast.parse(raw, filename="private_health_business")
        for node in ast.walk(tree):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            if isinstance(node, ast.ImportFrom) and node.level:
                raise CollectorFailure("business_contract")
            if any(name.split(".")[0] not in sys.stdlib_module_names for name in names):
                raise CollectorFailure("business_contract")
        module = ModuleType("_signaldesk_health_business")
        module.__file__ = str(source)
        sys.modules[module.__name__] = module
        exec(compile(tree, str(source), "exec"), module.__dict__)
        if not is_dataclass(module.NewsItem) or tuple(field.name for field in fields(module.NewsItem)) != (
                "title", "source", "link", "published", "query"):
            raise CollectorFailure("business_contract")
        for name in ("dedupe_news_items", "is_relevant_health_business_item", "generate_health_business_markdown"):
            if not callable(getattr(module, name, None)):
                raise CollectorFailure("business_contract")
        if not issubclass(module.NotEnoughNewsError, Exception):
            raise CollectorFailure("business_contract")
        return module
    except CollectorFailure:
        raise
    except Exception:
        raise CollectorFailure("business_contract") from None


def collect_markdown(collector_root: Path, target_date: date, max_items: int, *,
                     client: httpx.Client | None = None, now: datetime | None = None,
                     business: Any | None = None) -> CollectionResult:
    if not 1 <= max_items <= 100:
        raise CollectorFailure("invalid_max_items")
    current = now or datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise CollectorFailure("invalid_clock")
    current = current.astimezone(timezone.utc)
    owned_client = client is None
    if owned_client:
        client = httpx.Client(timeout=REQUEST_TIMEOUT, verify=True, trust_env=False, follow_redirects=False)
    try:
        bodies, providers = _fetch(client)
    finally:
        if owned_client:
            client.close()
    summary: dict[str, Any] = {"status": "failed", "date": target_date.isoformat(), "max_items": max_items,
                               "providers": providers, "article_count": 0}
    normalized = []
    for source, body in bodies:
        provider = next(value for value in providers if value["provider"] == source.id)
        try:
            values, counts = _normalize(source, body, current)
            normalized.extend(values)
            provider.update(status="parsed", **counts)
        except CollectorFailure as error:
            provider.update(status="failed", error=error.code)
    if not normalized:
        summary["error"] = "no_qualified_news"
        raise CollectorFailure("no_qualified_news", summary)
    try:
        policy = business or _load_business(collector_root)
        items = [policy.NewsItem(**value) for value in normalized]
        summary.update(normalized_count=len(normalized), windows=[])
        for hours in (24, 72):
            window_items = [item for item in items
                          if parsedate_to_datetime(item.published).astimezone(timezone.utc) >= current - timedelta(hours=hours)]
            # Apply the window before the original dedupe. An older copy from
            # the first provider must not suppress a fresh copy in another.
            unique = policy.dedupe_news_items(window_items)
            candidates = [item for item in unique if policy.is_relevant_health_business_item(item)]
            window = {"hours": hours, "input_count": len(window_items),
                      "deduped_count": len(unique), "relevant_count": len(candidates)}
            summary["windows"].append(window)
            if not candidates:
                continue
            try:
                markdown = policy.generate_health_business_markdown(candidates, target_date, min_items=1, max_items=max_items)
            except policy.NotEnoughNewsError:
                window["render_insufficient"] = True
                continue
            if not isinstance(markdown, str) or len(markdown.encode("utf-8")) > MAX_TOTAL_BYTES:
                raise CollectorFailure("business_contract")
            selected = sum(line.startswith("### ") for line in markdown.splitlines())
            if not 1 <= selected <= max_items:
                raise CollectorFailure("business_contract")
            summary.update(status="prepared", window_hours=hours, article_count=selected,
                           deduped_count=len(unique), relevant_count=len(candidates),
                           markdown_bytes=len(markdown.encode("utf-8")),
                           markdown_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest())
            return CollectionResult(markdown, summary)
    except CollectorFailure as error:
        summary["error"] = error.code
        raise CollectorFailure(error.code, summary) from None
    except Exception:
        summary["error"] = "business_contract"
        raise CollectorFailure("business_contract", summary) from None
    summary["error"] = "no_qualified_news"
    raise CollectorFailure("no_qualified_news", summary)


def _private_output(output_dir: Path) -> Path:
    path = Path(output_dir)
    try:
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise CollectorFailure("private_output_required")
        if os.name == "posix" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise CollectorFailure("private_output_required")
    except OSError:
        raise CollectorFailure("private_output_required") from None
    return path


def _atomic_file(directory: Path, name: str, data: bytes):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".health-rss-", suffix=".tmp",
                                         dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        target = directory / name
        if target.is_symlink():
            raise CollectorFailure("private_output_required")
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_outputs(output_dir: Path, target_date: date, result: CollectionResult) -> None:
    directory = _private_output(output_dir)
    _atomic_file(directory, target_date.isoformat() + ".md", result.markdown.encode("utf-8"))
    _atomic_file(directory, "summary.json", (json.dumps(result.summary, ensure_ascii=True, indent=2) + "\n").encode("utf-8"))


def _scrub_cli_environment():
    keep = {"PATH", "LANG", "LANGUAGE", "TZ", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
            "SYSTEMDRIVE", "TEMP", "TMP", "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED"}
    for name in tuple(os.environ):
        if name.upper() not in keep and not name.upper().startswith("LC_"):
            del os.environ[name]


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({"status": "failed", "error": "invalid_arguments"}))
        raise SystemExit(2)


class _DiscardOutput:
    def write(self, value):
        return len(value)

    def flush(self):
        pass


def main(argv=None) -> int:
    parser = _SafeParser(description=__doc__)
    parser.add_argument("--collector-root", type=Path, required=True)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-items", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        _private_output(args.output_dir)
        # Legacy diagnostics, including unexpected exceptions containing item
        # text, must never enter the public collector's JSON channel.
        with redirect_stdout(_DiscardOutput()), redirect_stderr(_DiscardOutput()):
            result = collect_markdown(args.collector_root, args.date, args.max_items)
        write_outputs(args.output_dir, args.date, result)
        print(json.dumps(result.summary, ensure_ascii=True))
        return 0
    except CollectorFailure as error:
        summary = error.summary
        try:
            directory = _private_output(args.output_dir)
            _atomic_file(directory, "summary.json", (json.dumps(summary, ensure_ascii=True, indent=2) + "\n").encode())
        except Exception:
            pass
        print(json.dumps(summary, ensure_ascii=True))
        return 1
    except Exception:
        print(json.dumps({"status": "failed", "error": "output_error"}))
        return 1


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    _scrub_cli_environment()
    raise SystemExit(main())
