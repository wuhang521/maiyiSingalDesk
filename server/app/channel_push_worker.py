"""Daily smart-channel collection and opt-in private WeChat delivery."""

from dataclasses import dataclass, field
from datetime import date, datetime, time as clock_time, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import time
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from .channel_aliases import SmartChannelAlias
from .channel_subscription_models import ChannelPushConfig, ChannelPushDelivery, ChannelSubscription, ChannelWebsiteSnapshot
from .channel_subscriptions import active_wechat_binding, binding_fingerprint, channel_push_settings
from .channel_website import fetch_website_items
from .config import get_settings
from .database import SessionLocal
from .models import Article, SmartChannel, Staff, UserAccount, utcnow
from .page_access import can_access


BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")
CHANNEL_PAGE = frozenset({"channels"})
WEBHOOK_ROUTE = "signaldesk-channel-weixin"
WEBHOOK_EVENT = "channel.digest"
MESSAGE_BYTES = 3500
MESSAGE_CHARS = 1800
SEND_BATCH_LIMIT = 20
SEND_BATCH_INTERVAL_SECONDS = 60
# Hermes recognizes local paths, MEDIA tags and image markup in ordinary text.
# Keep those characters inert in scraped prose; URLs are encoded separately.
PLAIN_TEXT = str.maketrans({"/": "／", "\\": "＼", ":": "：", "<": "＜", ">": "＞", "[": "［", "]": "］", "`": "｀"})


@dataclass(frozen=True)
class Rule:
    channel_id: str
    name: str
    schedule: str
    max_items: int
    source_type: str
    source_url: str | None
    selector: str | None
    include_terms: tuple[str, ...]
    exclude_terms: tuple[str, ...]
    revision: datetime | None
    allowed_user_ids: frozenset[int] | None = field(default=None, compare=False)


def read_rule(db, channel_id: str, *, lock: bool = False) -> Rule | None:
    channel = db.get(SmartChannel, channel_id, with_for_update=lock, populate_existing=lock)
    if not channel or channel.archived_at is not None or db.get(SmartChannelAlias, channel_id):
        return None
    config = db.get(ChannelPushConfig, channel_id, with_for_update=lock)
    value = channel_push_settings(channel, config)
    if not value["enabled"]:
        return None
    allowed = None if channel.subscription_user_ids is None else frozenset(channel.subscription_user_ids)
    return Rule(channel_id, value["name"], value["schedule"], value["max_items"], value["source_type"],
                value["source_url"], value["selector"], tuple(value["include_terms"]), tuple(value["exclude_terms"]),
                config.updated_at if config else None, allowed)


def item_hash(parts) -> str:
    body = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def text_line(value: str, length: int) -> str:
    return " ".join(value.split())[:length]


def wechat_text(value: str, length: int) -> str:
    return text_line(re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", value), length).translate(PLAIN_TEXT)


def public_link(value: str | None) -> str:
    if not value or len(value) > 1000 or any(char.isspace() or ord(char) < 32 for char in value):
        return ""
    try:
        target = urlsplit(value)
        if target.scheme in {"http", "https"} and target.hostname and not target.username and not target.password:
            return value
    except ValueError:
        pass
    return ""


def wechat_link(value: str) -> str:
    if not public_link(value):
        return ""
    try:
        target = urlsplit(value)
        host = (target.hostname or "").encode("idna").decode("ascii")
        if any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
               for label in host.rstrip(".").split(".")):
            return ""
        authority = f"{host}:{target.port}" if target.port else host
        path = quote(target.path, safe="/%-._~")
        # A slash after one of these characters can look like the start of a
        # bare local path to the Weixin adapter. Encode the preceding character.
        path = re.sub(r"[%~\-](?=/)", lambda match: f"%{ord(match[0]):02X}", path)
        link = urlunsplit((target.scheme, authority, path,
                          quote(target.query, safe="=&%+~-._"), quote(target.fragment, safe="%-._~")))
        return link if len(link) <= 1000 else ""
    except ValueError:
        return ""


def website_entries(rule: Rule, fetch) -> list[dict]:
    values = {}
    for item in fetch(rule.source_url, rule.selector, rule.max_items):
        title, body = text_line(item.title, 300), text_line(item.text, 800)
        link = public_link(item.url) or public_link(rule.source_url)
        searchable = f"{title} {body}".casefold()
        if rule.include_terms and not any(term.casefold() in searchable for term in rule.include_terms):
            continue
        if any(term.casefold() in searchable for term in rule.exclude_terms):
            continue
        if title or body:
            key = item_hash([title, body, link])
            values[key] = {"fingerprint": key, "title": title or body[:300], "text": body, "url": link}
    return list(values.values())[:rule.max_items]


def collect_website(factory, rule: Rule, day: date, now: datetime, fetch) -> None:
    if not rule.source_url or not rule.selector or rule.revision is None:
        return
    key = (rule.channel_id, day)
    source_fingerprint = item_hash([rule.source_url, rule.selector, rule.max_items, rule.include_terms, rule.exclude_terms])
    with factory() as db:
        current = db.get(ChannelWebsiteSnapshot, key)
        has_sample = current is not None and current.source_fingerprint == source_fingerprint
        if has_sample and current.config_updated_at == rule.revision:
            return
    entries = None
    if not has_sample:
        try:
            entries = website_entries(rule, fetch)
        except ValueError:
            print("channel-push: website source could not be read; will retry", flush=True)
            return

    with factory() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if read_rule(db, rule.channel_id, lock=True) != rule:
            return
        current = db.get(ChannelWebsiteSnapshot, key, with_for_update=True)
        if current and current.source_fingerprint == source_fingerprint:
            current.config_updated_at = rule.revision
            db.commit()
            return
        if entries is None:
            return
        previous = db.scalar(select(ChannelWebsiteSnapshot).where(
            ChannelWebsiteSnapshot.channel_id == rule.channel_id,
            ChannelWebsiteSnapshot.day < day,
        ).order_by(ChannelWebsiteSnapshot.day.desc()).limit(1))
        if current or (previous and previous.source_fingerprint != source_fingerprint):
            previous = None
        previously_seen = {item["fingerprint"] for item in previous.items} if previous else set()
        for item in entries:
            item["changed"] = previous is not None and item["fingerprint"] not in previously_seen
            article_id = "web:" + item_hash([rule.channel_id, item["fingerprint"]])
            if db.get(Article, article_id) is None:
                # The desktop reader treats article bodies as Markdown. Preserve
                # selected page text literally rather than rendering its image tags.
                plain_body = re.sub(r"([\\`*_{}\[\]<>()#+.!|~])", r"\\\1", item["text"])
                source = (urlsplit(rule.source_url).hostname or "网页")[:240]
                db.add(Article(id=article_id, channel_id=rule.channel_id, title=item["title"], body=plain_body,
                               source=source, click_url=item["url"] or None,
                               tags=["网页监测"], published_at=now, created_at=now, updated_at=now,
                               content_hash=item["fingerprint"], channel_push_trusted=True))
        if current is None:
            current = ChannelWebsiteSnapshot(channel_id=rule.channel_id, day=day)
            db.add(current)
        current.items = entries
        current.fingerprint = item_hash(sorted(item["fingerprint"] for item in entries))
        current.source_fingerprint = source_fingerprint
        current.changed = any(item["changed"] for item in entries)
        current.config_updated_at = rule.revision
        current.updated_at = now
        try:
            db.commit()
        except IntegrityError:
            # Another sampler may have written this date while SQLite was unlocked.
            db.rollback()
            if db.get(ChannelWebsiteSnapshot, key) is None:
                raise


def opted_in(db, rule: Rule, user_id: int, *, lock: bool = False):
    if rule.allowed_user_ids is not None and user_id not in rule.allowed_user_ids:
        return None
    subscription = db.get(ChannelSubscription, (rule.channel_id, user_id), with_for_update=lock)
    user = db.get(UserAccount, user_id, with_for_update=lock)
    if not subscription or not user or not user.enabled or not can_access(user.allowed_pages, user.role, CHANNEL_PAGE):
        return None
    staff = db.get(Staff, user.staff_id, with_for_update=lock)
    if not staff or not staff.enabled or staff.removed_at is not None:
        return None
    binding = active_wechat_binding(db, staff.id, lock=lock)
    if not binding or binding.id != subscription.binding_id or binding_fingerprint(binding) != subscription.binding_fingerprint:
        return None
    return subscription, binding


def article_entries(db, rule: Rule, start: datetime, now: datetime) -> list[dict]:
    query = select(Article).where(
        Article.channel_id == rule.channel_id, Article.channel_push_trusted.is_(True),
        Article.published_at >= start, Article.published_at <= now,
    )
    fields = (func.lower(Article.title), func.lower(Article.body), func.lower(Article.source))
    if rule.include_terms:
        query = query.where(or_(*(field.contains(term.lower(), autoescape=True)
                                  for term in rule.include_terms for field in fields)))
    for term in rule.exclude_terms:
        query = query.where(~or_(*(field.contains(term.lower(), autoescape=True) for field in fields)))
    articles = db.scalars(query.order_by(Article.published_at.desc(), Article.id).limit(rule.max_items)).all()
    return [{"title": text_line(article.title, 200), "text": text_line(article.body, 180),
             "url": public_link(article.click_url)} for article in articles]


def digest(db, rule: Rule, day: date, now: datetime, subscription, binding) -> str:
    since = max(subscription.created_at, binding.created_at, binding.updated_at)
    if rule.source_type == "website":
        snapshot = db.get(ChannelWebsiteSnapshot, (rule.channel_id, day))
        if not snapshot or not snapshot.changed or snapshot.config_updated_at != rule.revision or snapshot.updated_at < since:
            return ""
        entries = [item for item in snapshot.items if item["changed"]]
    else:
        midnight = datetime.combine(day, clock_time(), BEIJING).astimezone(timezone.utc).replace(tzinfo=None)
        entries = article_entries(db, rule, max(midnight, since), now)

    if not entries:
        return ""
    message = f"【迈医协同】{wechat_text(rule.name, 160)}\n{day:%Y-%m-%d} · {len(entries)} 条新情报"

    def fits(candidate: str) -> bool:
        # Leave room for the count of entries omitted from this digest.
        return len(candidate.encode()) <= MESSAGE_BYTES - 180 and len(candidate) <= MESSAGE_CHARS - 60

    for index, item in enumerate(entries, 1):
        title = wechat_text(item["title"], 200)
        line = f"\n\n{index}. {title}"
        excerpt = wechat_text(item["text"], 220)
        if excerpt and excerpt != title:
            line += f"\n{excerpt}"
        link = wechat_link(item["url"])
        if link and fits(message + line + "\n" + link):
            line += f"\n{link}"
        # Keep one digest within the WeChat adapter's single-message size.
        if not fits(message + line):
            message += f"\n\n另有 {len(entries) - index + 1} 条，请在迈医协同查看频道。"
            break
        message += line
    return message


def send_digest(factory, rule: Rule, user_id: int, day: date, now: datetime, resolve_send) -> bool:
    with factory() as db:
        recipient = opted_in(db, rule, user_id)
        if not recipient:
            return False
        subscription, binding = recipient
        send = resolve_send(binding.recipient_id)
        if send is None:
            return False
        message = digest(db, rule, day, now, subscription, binding)
        if not message:
            return False
        opted_at, identity = subscription.created_at, subscription.binding_fingerprint
        binding_updated_at = binding.updated_at
        delivery = ChannelPushDelivery(channel_id=rule.channel_id, user_id=user_id, day=day,
                                       created_at=now, status="unknown")
        db.add(delivery)
        try:
            db.flush()
            delivery_id = delivery.id
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(select(ChannelPushDelivery.id).where(ChannelPushDelivery.channel_id == rule.channel_id,
                ChannelPushDelivery.user_id == user_id, ChannelPushDelivery.day == day))
            if existing is None:
                raise
            return False

    # Commit the unique claim before transport. An interrupted or ambiguous send
    # stays unknown and is never replayed. Hold locks through transport so a
    # successful opt-out/disable cannot race with a not-yet-dispatched digest.
    with factory() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.execute(update(ChannelPushDelivery).where(ChannelPushDelivery.id == delivery_id).values(status="unknown"))
        current = read_rule(db, rule.channel_id, lock=True)
        # Check the latest audience without invalidating queued sends to members who remain eligible.
        recipient = opted_in(db, current, user_id, lock=True) if current == rule else None
        delivery = db.get(ChannelPushDelivery, delivery_id, with_for_update=True)
        if delivery is None:
            return False
        if (not recipient or recipient[0].created_at != opted_at or recipient[0].binding_fingerprint != identity
                or recipient[1].updated_at != binding_updated_at):
            delivery.status = "cancelled"
        else:
            request_id = "channel-" + item_hash([rule.channel_id, user_id, day.isoformat()])
            try:
                outcome = send(recipient[1].recipient_id, message, request_id)
            except Exception:
                outcome = "unknown"
            delivery.status = outcome if outcome in {"sent", "failed"} else "unknown"
        delivery.finished_at = utcnow()
        db.commit()
    return True


def run_cycle(factory=SessionLocal, *, now=None, fetch=fetch_website_items, send=None, resolve_send=None,
              wait=time.sleep) -> int:
    now = now if now is not None else utcnow()
    if now.tzinfo:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    local = now.replace(tzinfo=timezone.utc).astimezone(BEIJING)
    day = local.date()
    with factory() as db:
        ids = db.scalars(select(SmartChannel.id).where(
            SmartChannel.archived_at.is_(None),
            or_(exists(select(ChannelSubscription.user_id).where(ChannelSubscription.channel_id == SmartChannel.id)),
                exists(select(ChannelPushConfig.channel_id).where(ChannelPushConfig.channel_id == SmartChannel.id,
                    ChannelPushConfig.source_type == "website", ChannelPushConfig.enabled.is_(True)))),
        ).order_by(SmartChannel.id)).all()
        rules = [rule for channel_id in ids if (rule := read_rule(db, channel_id)) is not None
                 and local.time() >= clock_time.fromisoformat(rule.schedule)]

    for rule in rules:
        if rule.source_type == "website":
            collect_website(factory, rule, day, now, fetch)
    if send is None and resolve_send is None:
        return 0
    sender_for_recipient = resolve_send if resolve_send is not None else lambda _: send

    attempts = 0
    batch_attempts = 0
    for rule in rules:
        with factory() as db:
            already_claimed = exists(select(ChannelPushDelivery.id).where(ChannelPushDelivery.channel_id == rule.channel_id,
                ChannelPushDelivery.user_id == ChannelSubscription.user_id, ChannelPushDelivery.day == day))
            users = db.scalars(select(ChannelSubscription.user_id).where(
                ChannelSubscription.channel_id == rule.channel_id, ~already_claimed,
            ).order_by(ChannelSubscription.user_id)).all()
        for user_id in users:
            if batch_attempts >= SEND_BATCH_LIMIT:
                # Stay in this Beijing day's cycle while pacing later sends.
                # Both collection and the last send have closed their sessions.
                wait(SEND_BATCH_INTERVAL_SECONDS)
                batch_attempts = 0
            attempted = send_digest(factory, rule, user_id, day, now, sender_for_recipient)
            attempts += attempted
            batch_attempts += attempted
    return attempts


def valid_webhook_url(url: str, *, profile: str | None = None) -> bool:
    try:
        target = urlsplit(url)
        transport_ok = target.scheme == "https" or target.scheme == "http" and target.hostname in {"127.0.0.1", "localhost", "::1"}
        if not (transport_ok and target.hostname and not target.username and not target.password
                and not target.query and not target.fragment):
            return False
        if profile is None:
            return target.path.endswith("/webhooks/" + WEBHOOK_ROUTE)
        return (re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", profile) is not None
                and target.path == f"/p/{profile}/webhooks/{WEBHOOK_ROUTE}-{profile}")
    except ValueError:
        return False


class HermesWechatSender:
    def __init__(self, url: str, secret: str, *, profile: str | None = None, transport=None):
        self.url = url.strip()
        self.secret = secret.strip()
        if not self.secret or not valid_webhook_url(self.url, profile=profile):
            raise ValueError("Invalid channel WeChat webhook configuration")
        self.route = f"{WEBHOOK_ROUTE}-{profile}" if profile is not None else WEBHOOK_ROUTE
        self.transport = transport

    def __call__(self, recipient_id: str, message: str, request_id: str) -> str:
        body = json.dumps({"event_type": WEBHOOK_EVENT, "recipient_id": recipient_id, "message": message},
                          ensure_ascii=False, separators=(",", ":")).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(self.secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-Webhook-Timestamp": stamp,
                   "X-Webhook-Signature-V2": signature, "X-Request-ID": request_id}
        try:
            with httpx.Client(timeout=httpx.Timeout(15, connect=5), trust_env=False, follow_redirects=False,
                              transport=self.transport) as client:
                with client.stream("POST", self.url, content=body, headers=headers) as response:
                    if response.status_code in {400, 401, 403, 404, 413, 429}:
                        return "failed"
                    if response.status_code != 200:
                        return "unknown"
                    result_body = bytearray()
                    deadline = time.monotonic() + 20
                    for chunk in response.iter_bytes():
                        result_body.extend(chunk)
                        if len(result_body) > 16_000 or time.monotonic() > deadline:
                            return "unknown"
                    result = json.loads(result_body)
            if isinstance(result, dict) and (result.get("status"), result.get("route"), result.get("target"),
                    result.get("delivery_id")) == ("delivered", self.route, "weixin", request_id):
                return "sent"
        except httpx.ConnectError:
            return "failed"
        except (httpx.HTTPError, ValueError):
            pass
        return "unknown"


def unique_config_keys(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("Duplicate channel WeChat route configuration key")
        data[key] = value
    return data


def profile_wechat_senders(path: str, *, transport=None):
    with Path(path).open("rb") as config:
        data = config.read(131_073)
    if len(data) > 131_072:
        raise ValueError("Channel WeChat routes file is too large")
    payload = json.loads(data, object_pairs_hook=unique_config_keys)
    if not isinstance(payload, dict) or set(payload) != {"bindings", "routes"}:
        raise ValueError("Invalid channel WeChat routes file")
    bindings, routes = payload["bindings"], payload["routes"]
    if not isinstance(bindings, dict) or not isinstance(routes, dict):
        raise ValueError("Invalid channel WeChat routes or bindings")
    senders = {}
    for profile, route in routes.items():
        if (not isinstance(route, dict) or set(route) != {"url", "secret"}
                or not all(isinstance(value, str) for value in route.values())):
            raise ValueError("Invalid channel WeChat profile route")
        senders[profile] = HermesWechatSender(route["url"], route["secret"], profile=profile, transport=transport)
    recipient_senders = {}
    for recipient_hash, profile in bindings.items():
        if not re.fullmatch(r"[0-9a-f]{64}", recipient_hash) or not isinstance(profile, str) or profile not in senders:
            raise ValueError("Invalid channel WeChat recipient profile")
        recipient_senders[recipient_hash] = senders[profile]

    def sender_for(recipient_id: str):
        return recipient_senders.get(hashlib.sha256(recipient_id.encode()).hexdigest())

    return sender_for


def main() -> None:
    settings = get_settings()
    if not settings.channel_push_enabled:
        print("channel-push: disabled", flush=True)
        return
    route_was_unavailable = False
    while True:
        send = resolve_send = None
        try:
            if settings.channel_wechat_routes_file:
                resolve_send = profile_wechat_senders(settings.channel_wechat_routes_file)
            else:
                send = HermesWechatSender(settings.channel_wechat_webhook_url, settings.channel_wechat_webhook_secret)
            route_was_unavailable = False
        except (OSError, ValueError, RecursionError):
            if not route_was_unavailable:
                print("channel-push: WeChat route unavailable; collecting websites only", flush=True)
            route_was_unavailable = True
        try:
            run_cycle(send=send, resolve_send=resolve_send)
        except Exception:
            print("channel-push: cycle failed", flush=True)
        time.sleep(60)


if __name__ == "__main__":
    main()
