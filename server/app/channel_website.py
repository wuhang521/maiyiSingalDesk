"""Collect items from bounded public pages, feeds, and official notice APIs."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
import http.client
from html.parser import HTMLParser
from io import StringIO
import ipaddress
import json
import re
import socket
import ssl
from threading import BoundedSemaphore, Timer
import time
from urllib.parse import quote, unquote_plus, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import certifi


MAX_BYTES = 1024 * 1024
MAX_NODES = 30_000
MAX_DEPTH = 96
FETCH_SECONDS = 15
_FETCH_SLOTS = BoundedSemaphore(4)
_FETCH_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="channel-web")
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_HIDDEN = frozenset("script style template noscript svg iframe object".split())
_HEADINGS = frozenset("h1 h2 h3 h4 h5 h6".split())
_BLOCKS = _HEADINGS | frozenset("address article aside blockquote br dd div dl dt figcaption figure footer header hr li main nav ol p section table td tr ul".split())
_SIMPLE_SELECTOR = re.compile(r"([a-zA-Z][a-zA-Z0-9-]*)?((?:[.#][\w-]+)*)")
_AUTH_PARAMETER = re.compile(
    r"(?:^|[-_])(?:token|secret|key|password|passwd|authorization|credential|signature|session|sig)(?:$|[-_])",
    re.I,
)
_IPV6_TRANSITION = tuple(
    ipaddress.ip_network(prefix) for prefix in ("64:ff9b::/96", "64:ff9b:1::/48", "2001::/32", "2002::/16")
)
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml", "application/rdf+xml")
_XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
_FDA_SOURCE = "https://api.fda.gov/"
_FDA_CATEGORIES = {"food": "食品", "drug": "药品", "device": "医疗器械"}
_CDC_FSIS_FEED = "https://tools.cdc.gov/api/v2/resources/media/379374.rss"
_CDC_FSIS_SELECTOR = "@cdc-fsis"
_CDC_RECALL_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cdc-recall")
_CDC_TRACKED_RECALL = re.compile(
    r"https://tools\.cdc\.gov/api/embed/downloader/download\.asp\?m=379374&c=([0-9]{1,10})"
)


class WebsiteFetchError(ValueError):
    """The remote page could not safely provide selected content."""


class WebsiteResponseTooLarge(WebsiteFetchError):
    """The source response exceeded the collector's byte limit."""


@dataclass(frozen=True)
class WebsiteItem:
    title: str
    text: str
    url: str


@dataclass(frozen=True)
class _Target:
    scheme: str
    host: str
    path: str
    query: str

    @property
    def port(self) -> int:
        return 443 if self.scheme == "https" else 80

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return urlunsplit((self.scheme, host, self.path, self.query, ""))


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and (ip.ipv4_mapped or any(ip in network for network in _IPV6_TRANSITION)):
        return False
    return ip.is_global and not (ip.is_multicast or ip.is_reserved) and str(ip) != "168.63.129.16"


def _public_host(host: str) -> str:
    message = "请使用公开网站的域名或公网 IP。"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            name = host.rstrip(".").encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError(message) from None
        labels = name.split(".")
        if (len(name) > 253 or len(labels) < 2 or name.endswith((".local", ".localhost", ".internal", ".home.arpa"))
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
                or all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label) for label in labels)):
            raise ValueError(message) from None
        return name
    if "%" in host or not _is_public_ip(host):
        raise ValueError(message)
    return str(address)


def _credential_parameter(key: str) -> bool:
    normalized = re.sub(r"[-_]", "", key).lower()
    return bool(_AUTH_PARAMETER.search(key)) or normalized in {
        "accesskey", "accesstoken", "apikey", "authtoken", "clientsecret", "refreshtoken", "sessionid",
        "sid", "xamzcredential",
    }


def _target(url: str, *, clean_link: bool = False) -> _Target:
    value = url.strip()
    if not value or len(value) > 2048 or re.search(r"[\x00-\x20\x7f\\]", value):
        raise ValueError("请输入有效的公开网页地址。")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ValueError("请输入有效的公开网页地址。") from None
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
            or parts.password is not None or port not in (None, 443 if parts.scheme == "https" else 80)):
        raise ValueError("请使用公开的 HTTP 或 HTTPS 网页地址，去掉账号、密码和自定义端口。")
    host = _public_host(parts.hostname)
    query = parts.query
    segments = re.split(r"[&;]", query)
    public_segments = [
        segment for segment in segments if not _credential_parameter(unquote_plus(segment.partition("=")[0]))
    ]
    if len(public_segments) != len(segments):
        if not clean_link:
            raise ValueError("网页地址不能包含访问密钥或登录参数。")
        query = "&".join(public_segments)
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(query, safe="/?%:@!$&'()*+,;=-._~")
    return _Target(parts.scheme, host, path, query)


@dataclass(frozen=True)
class _SelectorStep:
    tag: str | None
    element_id: str | None
    classes: frozenset[str]
    direct: bool

    def matches(self, tag: str, attrs: dict[str, str | None], classes: frozenset[str]) -> bool:
        return ((self.tag is None or self.tag == tag)
                and (self.element_id is None or self.element_id == attrs.get("id"))
                and self.classes <= classes)


def _selector(value: str) -> tuple[_SelectorStep, ...]:
    message = "请输入简单 CSS 选择器，例如 #news-list a 或 main > .notice。"
    value = value.strip()
    if not value or len(value) > 240:
        raise ValueError(message)
    parts = re.split(r"(\s*>\s*|\s+)", value)
    if len(parts) > 11:
        raise ValueError(message)
    steps = []
    for index in range(0, len(parts), 2):
        part = parts[index]
        match = _SIMPLE_SELECTOR.fullmatch(part)
        if not part or not match:
            raise ValueError(message)
        tag, attributes = match.groups()
        identifiers = re.findall(r"#([\w-]+)", attributes)
        if len(identifiers) > 1:
            raise ValueError(message)
        steps.append(_SelectorStep(tag.lower() if tag else None, identifiers[0] if identifiers else None,
                                   frozenset(re.findall(r"\.([\w-]+)", attributes)),
                                   index > 0 and ">" in parts[index - 1]))
    return tuple(steps)


def validate_website_config(url: str, selector: str) -> None:
    """Validate configuration without resolving DNS or making requests."""
    if _source_mode(_target(url), selector) == "html":
        _selector(selector)


def _source_mode(target: _Target, selector: str) -> str:
    value = selector.strip()
    if value == "@rss":
        return value
    if value == _CDC_FSIS_SELECTOR:
        if target.url != _CDC_FSIS_FEED:
            raise ValueError("该采集方式仅支持 CDC 官方食品召回订阅源。")
        return value
    if value == "@openfda":
        if target.url != _FDA_SOURCE:
            raise ValueError("该采集方式仅支持官方 HTTPS 接口 api.fda.gov。")
        return value
    if value != "@fsa-alerts":
        return "html"
    if (target.scheme, target.host, target.path) != ("https", "data.food.gov.uk", "/food-alerts/id.json"):
        raise ValueError("该采集方式仅支持官方 HTTPS 接口 data.food.gov.uk/food-alerts/id.json。")
    return value


def _resolve_public(host: str, port: int) -> list[tuple]:
    candidates = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not candidates or any(not _is_public_ip(info[4][0]) for info in candidates):
        raise WebsiteFetchError("网址解析到了非公网地址。")
    return list(dict.fromkeys(candidates))


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise WebsiteFetchError("读取网页超时，请稍后重试。")
    return remaining


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, target: _Target, addresses: list[tuple], deadline: float):
        super().__init__(target.host, target.port, timeout=min(5, _remaining(deadline)))
        self.target = target
        self.addresses = addresses
        self.deadline = deadline

    def connect(self):
        for family, kind, protocol, _, sockaddr in self.addresses:
            raw = socket.socket(family, kind, protocol)
            try:
                raw.settimeout(min(5, _remaining(self.deadline)))
                raw.connect(sockaddr)
                if raw.getpeername()[0] != sockaddr[0] or not _is_public_ip(raw.getpeername()[0]):
                    raise WebsiteFetchError("网页服务器连接地址无效。")
                if self.target.scheme == "https":
                    raw.settimeout(min(5, _remaining(self.deadline)))
                    tls_context = ssl.create_default_context(cafile=certifi.where())
                    self.sock = tls_context.wrap_socket(raw, server_hostname=self.host)
                else:
                    self.sock = raw
                return
            except OSError:
                raw.close()
            except BaseException:
                raw.close()
                raise
        raise WebsiteFetchError("无法连接网页服务器。")


def _shutdown(connection: socket.socket) -> None:
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _decode_page(data: bytes, content_type: str) -> str:
    declared = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.I)
    if declared is None:
        declared = re.search(r"<meta\b[^>]{0,500}\bcharset\s*=\s*[\"']?([\w-]+)",
                             data[:4096].decode("ascii", errors="ignore"), re.I)
    charset = declared[1].lower() if declared else ""
    charsets = {"gbk": "gb18030", "gb2312": "gb18030", "gb18030": "gb18030",
                "big5": "big5", "windows-1252": "cp1252", "iso-8859-1": "latin-1"}
    for encoding in dict.fromkeys([charsets.get(charset, "utf-8-sig"), "utf-8-sig", "gb18030"]):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise WebsiteFetchError("无法识别网页文字编码。")


def _download_document(target: _Target, deadline: float, media_types: tuple[str, ...], invalid: str) -> str:
    addresses = _resolve_public(target.host, target.port)
    connection = _PinnedConnection(target, addresses, deadline)
    timer = None
    try:
        connection.connect()
        # Interrupt a slow response even if the peer continuously drips bytes.
        timer = Timer(_remaining(deadline), _shutdown, (connection.sock,))
        timer.daemon = True
        timer.start()
        connection.request("GET", urlunsplit(("", "", target.path, target.query, "")), headers={
            "User-Agent": "SignalDesk-Channel/1.0", "Accept": ", ".join(media_types),
            "Accept-Encoding": "identity", "Connection": "close",
        })
        with connection.getresponse() as response:
            if 300 <= response.status < 400:
                raise WebsiteFetchError("网页发生跳转，请改用跳转后的公开网址。")
            content_type = response.getheader("Content-Type", "")
            if response.status != 200 or content_type.split(";", 1)[0].strip().lower() not in media_types:
                raise WebsiteFetchError(invalid)
            if response.getheader("Content-Encoding", "identity").strip().lower() != "identity":
                raise WebsiteFetchError("网页返回了不支持的压缩格式。")
            if response.length is not None and response.length > MAX_BYTES:
                raise WebsiteResponseTooLarge("网页超过 1 MB，无法读取。")
            data = bytearray()
            while len(data) <= MAX_BYTES:
                _remaining(deadline)
                block = response.read1(min(65536, MAX_BYTES + 1 - len(data)))
                _remaining(deadline)
                if not block:
                    if response.length:
                        raise WebsiteFetchError("网页未传输完整，请稍后重试。")
                    return _decode_page(bytes(data), content_type)
                data.extend(block)
            raise WebsiteResponseTooLarge("网页超过 1 MB，无法读取。")
    except (OSError, http.client.HTTPException):
        raise WebsiteFetchError("读取网页失败，请检查公开网址或稍后重试。") from None
    finally:
        if timer is not None:
            timer.cancel()
        connection.close()


def _download_page(target: _Target, deadline: float) -> str:
    return _download_document(target, deadline, _HTML_TYPES, "网址未返回可读取的 HTML 网页。")


def _download_feed(target: _Target, deadline: float) -> str:
    return _download_document(target, deadline, _FEED_TYPES, "网址未返回可读取的 RSS 或 Atom 订阅源。")


def _download_notices(target: _Target, deadline: float) -> str:
    return _download_document(target, deadline, ("application/json",), "官方接口未返回可读取的 JSON 数据。")


@dataclass(slots=True)
class _Element:
    tag: str
    attrs: dict[str, str | None]
    hidden: bool
    matches: int
    ancestors: int
    children: list["_Element | str"] = field(default_factory=list)


class _PageParser(HTMLParser):
    def __init__(self, selector: tuple[_SelectorStep, ...]):
        super().__init__(convert_charrefs=True)
        self.selector = selector
        self.stack: list[_Element] = []
        self.selected: list[_Element] = []
        self.nodes = 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > MAX_NODES or len(self.stack) >= MAX_DEPTH:
            raise WebsiteFetchError("网页结构过大，无法读取。")
        if tag == "li" and self.stack and self.stack[-1].tag == "li":
            self.stack.pop()
        if tag in _BLOCKS and self.stack and self.stack[-1].tag == "p":
            self.stack.pop()
        parent = self.stack[-1] if self.stack else None
        attributes = dict(attrs)
        hidden = tag in _HIDDEN or "hidden" in attributes or bool(parent and parent.hidden)
        ancestors = (parent.matches | parent.ancestors) if parent else 0
        matches = 0
        if not hidden:
            classes = frozenset((attributes.get("class") or "").split())
            for index, step in enumerate(self.selector):
                previous = parent.matches if step.direct and parent else ancestors
                if (index == 0 or previous & (1 << (index - 1))) and step.matches(tag, attributes, classes):
                    matches |= 1 << index
        node = _Element(tag, attributes, hidden, matches, ancestors)
        if parent is not None and not hidden:
            parent.children.append(node)
        if matches & (1 << (len(self.selector) - 1)):
            self.selected.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if self.stack and not self.stack[-1].hidden:
            self.stack[-1].children.append(data)


def _page_link(source_url: str, href: str) -> str:
    try:
        candidate = urljoin(source_url, href.strip())
        link = _target(candidate, clean_link=True).url
    except ValueError:
        return ""
    return link


def _element_item(element: _Element, source_url: str) -> WebsiteItem | None:
    text_parts: list[str] = []
    heading: list[str] = []
    link = ""

    def collect(node: _Element, in_heading: bool = False) -> None:
        nonlocal link
        if node.tag == "a" and not link and node.attrs.get("href"):
            link = _page_link(source_url, node.attrs["href"])
        if node.tag in _BLOCKS:
            text_parts.append(" ")
        first_heading = in_heading or (node.tag in _HEADINGS and not heading)
        for child in node.children:
            if isinstance(child, str):
                text_parts.append(child)
                if first_heading:
                    heading.append(child)
            else:
                collect(child, first_heading)
        if node.tag in _BLOCKS:
            text_parts.append(" ")

    collect(element)
    text = _normalized_text(text_parts)[:1500]
    if not text:
        return None
    title = _normalized_text(heading) or text
    return WebsiteItem(title=title[:150], text=text, url=link or source_url)


def _normalized_text(parts: list[str]) -> str:
    return " ".join(re.sub(r"[\x00-\x08\x0e-\x1f\x7f]", "", "".join(parts)).split())


def _extract(html: str, target: _Target, selector: tuple[_SelectorStep, ...], limit: int) -> list[WebsiteItem]:
    parser = _PageParser(selector)
    parser.feed(html)
    parser.close()
    items = []
    seen = set()
    for element in parser.selected:
        item = _element_item(element, target.url)
        if item is None or item in seen:
            continue
        seen.add(item)
        items.append(item)
        if len(items) == limit:
            break
    if not items:
        raise WebsiteFetchError("没有找到选择器匹配的可读内容，请检查网页地址或选择器。")
    return items


def _xml_name(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _feed_root(document: str) -> ElementTree.Element:
    if "\0" in document or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", document, re.IGNORECASE):
        raise WebsiteFetchError("订阅源包含不支持的 XML 声明。")
    try:
        parsed = ElementTree.iterparse(StringIO(document), events=("start", "end"))
        depth = nodes = 0
        for event, _ in parsed:
            if event == "start":
                depth += 1
                nodes += 1
                if depth > MAX_DEPTH or nodes > MAX_NODES:
                    raise WebsiteFetchError("订阅源结构过大，无法读取。")
            else:
                depth -= 1
        return parsed.root
    except ElementTree.ParseError:
        raise WebsiteFetchError("无法解析 RSS 或 Atom 订阅源。") from None


def _feed_field(entry: ElementTree.Element, *names: str) -> str:
    for name in names:
        element = next((child for child in entry if _xml_name(child) == name), None)
        if element is not None:
            value = _normalized_text(list(element.itertext()))
            if value:
                return value
    return ""


def _feed_link(entry: ElementTree.Element, base: str, atom: bool) -> str:
    base = urljoin(base, entry.get(_XML_BASE, ""))
    for child in entry:
        if _xml_name(child) != "link" or (atom and child.get("rel", "alternate") != "alternate"):
            continue
        raw_link = child.get("href", "") if atom else (child.text or "")
        if raw_link and (link := _page_link(urljoin(base, child.get(_XML_BASE, "")), raw_link)):
            return link
    return ""


def _plain_notice_text(value: str, target: _Target) -> str:
    if "<" not in value:
        return value
    # Feed text can use escaped markup or CDATA. Apply the same hidden-element
    # handling as HTML channels before storing it as notification text.
    parser = _PageParser(_selector("main"))
    parser.feed(f"<main>{value}</main>")
    parser.close()
    item = _element_item(parser.selected[0], target.url)
    return item.text if item else ""


def _extract_feed(document: str, target: _Target, limit: int) -> list[WebsiteItem]:
    root = _feed_root(document)
    root_name = _xml_name(root)
    atom = root_name == "feed"
    container = root
    if root_name == "rss":
        container = next((child for child in root if _xml_name(child) == "channel"), None)
    if root_name not in {"rss", "feed", "RDF"} or container is None:
        raise WebsiteFetchError("网址未返回可读取的 RSS 或 Atom 订阅源。")
    base = urljoin(target.url, root.get(_XML_BASE, ""))
    if container is not root:
        base = urljoin(base, container.get(_XML_BASE, ""))
    items = []
    seen = set()
    for entry in container:
        if _xml_name(entry) != ("entry" if atom else "item"):
            continue
        link = _feed_link(entry, base, atom)
        title = _plain_notice_text(_feed_field(entry, "title"), target)
        if not link or not title:
            continue
        body = _plain_notice_text(_feed_field(entry, "summary", "description", "content", "encoded"), target)
        item = WebsiteItem(title[:150], (body or title)[:1500], link)
        if item in seen:
            continue
        items.append(item)
        seen.add(item)
        if len(items) == limit:
            break
    if not items:
        raise WebsiteFetchError("订阅源没有可读取的标题和原文链接。")
    return items


def _notice_value(record: dict, field: str) -> str:
    value = record.get(field)
    return value.strip() if isinstance(value, str) else ""


def _fsa_text(record: dict) -> str:
    problems = record.get("problem")
    risks = []
    if isinstance(problems, list):
        risks = [_notice_value(problem, "riskStatement") for problem in problems if isinstance(problem, dict)]
    return _normalized_text([
        " ".join(_notice_value(record, field) for field in ("notation", "created", "modified", "description")),
        " ", " ".join(risks),
    ])


def _extract_fsa_alerts(document: str, target: _Target, limit: int) -> list[WebsiteItem]:
    try:
        value = json.loads(document)
    except (ValueError, RecursionError):
        raise WebsiteFetchError("无法解析官方通知接口。") from None
    records = value.get("items") if isinstance(value, dict) else None
    if not isinstance(records, list):
        raise WebsiteFetchError("官方通知接口未返回预期的列表。")
    items = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        raw_link = _notice_value(record, "alertURL")
        link = _page_link(target.url, raw_link) if raw_link else ""
        title = _plain_notice_text(_normalized_text([_notice_value(record, "title")]), target)
        if urlsplit(link).hostname not in {"alerts.food.gov.uk", "www.food.gov.uk"} or not title or link in seen:
            continue
        text = _plain_notice_text(_fsa_text(record), target) or title
        items.append(WebsiteItem(title[:150], text[:1500], link))
        seen.add(link)
        if len(items) == limit:
            break
    if not items:
        raise WebsiteFetchError("官方通知接口没有可读取的标题和原文链接。")
    return items


def _cdc_recall_candidates(document: str) -> list[str]:
    root = _feed_root(document)
    channel = root.find("channel") if _xml_name(root) == "rss" else None
    if channel is None:
        raise WebsiteFetchError("CDC 食品召回订阅源格式无效。")
    item_ids = {}
    hinted = set()
    for entry in channel.findall("item"):
        match = _CDC_TRACKED_RECALL.fullmatch(_feed_field(entry, "link"))
        if not match:
            continue
        item_ids[match[1]] = None
        description = _feed_field(entry, "description").lower()
        if "fsis.usda.gov/" in description or "food safety and inspection service" in description:
            hinted.add(match[1])
    if not item_ids:
        raise WebsiteFetchError("CDC 食品召回订阅源没有可验证的条目。")
    # The agency logo/boilerplate narrows lookup traffic. Also check the
    # newest mixed entries in case an item's description omits the hint.
    ordered = list(item_ids)
    selected = set(ordered[:4]) | set([item_id for item_id in ordered if item_id in hinted][:8])
    return [item_id for item_id in ordered if item_id in selected]


def _extract_cdc_fsis_recall(document: str, item_id: str, target: _Target) -> WebsiteItem | None:
    try:
        payload = json.loads(document)
    except (ValueError, RecursionError):
        raise WebsiteFetchError("CDC 食品召回来源数据格式无效。") from None
    records = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise WebsiteFetchError("CDC 食品召回接口未返回对应来源。")
    record = records[0]
    if record.get("id") != int(item_id):
        raise WebsiteFetchError("CDC 食品召回记录标识不符。")
    try:
        origin = _target(_notice_value(record, "sourceUrl"), clean_link=True)
    except ValueError:
        return None
    if origin.host not in {"www.fsis.usda.gov", "fsis.usda.gov"}:
        return None
    path = origin.path.rstrip("/")
    if not re.fullmatch(r"/recalls-alerts/[a-zA-Z0-9-]+", path):
        return None
    link = urlunsplit(("https", "www.fsis.usda.gov", path, "", ""))
    title = _plain_notice_text(_normalized_text([_notice_value(record, "name")]), target)
    if not title:
        return None
    summary = _plain_notice_text(_normalized_text([_notice_value(record, "description")]), target)
    return WebsiteItem(title[:150], (summary or title)[:1500], link)


def _download_cdc_fsis_recall(item_id: str, target: _Target, deadline: float) -> WebsiteItem | None:
    _remaining(deadline)
    details = _target(f"https://tools.cdc.gov/api/v2/resources/media/{item_id}.json"
                      "?fields=id,name,description,sourceUrl")
    document = _download_document(details, deadline, ("application/json",), "CDC 食品召回接口无效。")
    return _extract_cdc_fsis_recall(document, item_id, target)


def _collect_cdc_fsis(target: _Target, deadline: float, limit: int) -> list[WebsiteItem]:
    document = _download_feed(target, deadline)
    # The RSS links identify CDC records. The metadata provides the original
    # agency link without needing to follow the RSS tracking redirect.
    item_ids = _cdc_recall_candidates(document)
    tasks = []
    items = {}
    try:
        for item_id in item_ids:
            tasks.append(_CDC_RECALL_POOL.submit(_download_cdc_fsis_recall, item_id, target, deadline))
        for task in tasks:
            item = task.result(timeout=_remaining(deadline))
            if item is not None:
                items.setdefault(item.url, item)
            if len(items) == limit:
                break
    except FutureTimeout:
        raise WebsiteFetchError("读取 CDC 食品召回来源超时，请稍后重试。") from None
    finally:
        for task in tasks:
            task.cancel()
    if not items:
        raise WebsiteFetchError("CDC 食品召回订阅源未返回可确认的 USDA FSIS 原文。")
    return list(items.values())


def _fda_recalls(kind: str, deadline: float) -> list[tuple[str, int, WebsiteItem]]:
    endpoint = f"{_FDA_SOURCE}{kind}/enforcement.json"
    try:
        document = _download_notices(_target(endpoint + "?sort=report_date%3Adesc&limit=10"), deadline)
    except WebsiteResponseTooLarge:
        document = _download_notices(_target(endpoint + "?sort=report_date%3Adesc&limit=3"), deadline)
    try:
        payload = json.loads(document)
    except (ValueError, RecursionError):
        raise WebsiteFetchError("无法解析 FDA 召回数据。") from None
    records = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise WebsiteFetchError("FDA 召回接口未返回预期的列表。")
    items = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        number = _notice_value(record, "recall_number")
        report_date = _notice_value(record, "report_date")
        product = _notice_value(record, "product_description")
        if not re.fullmatch(r"[A-Z][A-Z0-9-]{2,31}", number) or not re.fullmatch(r"\d{8}", report_date) or not product:
            continue
        query = urlencode({"search": f'recall_number.exact:"{number}"', "limit": 1})
        title = _normalized_text([f"{_FDA_CATEGORIES[kind]} {number} · {product}"])[:150]
        details = _normalized_text([
            f"{report_date[:4]}-{report_date[4:6]}-{report_date[6:]} ", _notice_value(record, "classification"),
            " ", _notice_value(record, "reason_for_recall"),
        ])
        items.append((report_date, index, WebsiteItem(title, details[:1500], f"{endpoint}?{query}")))
    if not items:
        raise WebsiteFetchError("FDA 召回接口没有可读取的记录编号和产品信息。")
    return items


def _collect_fda_recalls(limit: int, deadline: float) -> list[WebsiteItem]:
    records = [record for kind in _FDA_CATEGORIES for record in _fda_recalls(kind, deadline)]
    records.sort(key=lambda record: (record[0], -record[1]), reverse=True)
    return list({item.url: item for _, _, item in records}.values())[:limit]


def _collect_source(target: _Target, mode: str, steps: tuple[_SelectorStep, ...], limit: int,
                    deadline: float) -> list[WebsiteItem]:
    if mode == "html":
        return _extract(_download_page(target, deadline), target, steps, limit)
    if mode == "@rss":
        return _extract_feed(_download_feed(target, deadline), target, limit)
    if mode == _CDC_FSIS_SELECTOR:
        return _collect_cdc_fsis(target, deadline, limit)
    if mode == "@openfda":
        return _collect_fda_recalls(limit, deadline)
    return _extract_fsa_alerts(_download_notices(target, deadline), target, limit)


def fetch_website_items(url: str, selector: str, limit: int) -> list[WebsiteItem]:
    target = _target(url)
    mode = _source_mode(target, selector)
    steps = _selector(selector) if mode == "html" else ()
    if not 1 <= limit <= 100:
        raise ValueError("网页频道每次可读取 1 至 100 条。")
    if not _FETCH_SLOTS.acquire(blocking=False):
        raise WebsiteFetchError("网页采集繁忙，请稍后重试。")
    deadline = time.monotonic() + FETCH_SECONDS
    try:
        task = _FETCH_POOL.submit(_collect_source, target, mode, steps, limit, deadline)
    except BaseException:
        _FETCH_SLOTS.release()
        raise
    task.add_done_callback(lambda _: _FETCH_SLOTS.release())
    try:
        return task.result(timeout=_remaining(deadline))
    except FutureTimeout:
        task.cancel()
        raise WebsiteFetchError("读取网页超时，请稍后重试。") from None
    except (OSError, UnicodeError):
        raise WebsiteFetchError("读取网页失败，请检查公开网址或稍后重试。") from None
