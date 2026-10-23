"""Bounded extraction. Channels short links are never treated as video files."""
import http.client
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import tempfile
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from .config import get_settings

MAX_TEXT = 1_000_000
TEXT_EXTENSIONS = {".txt", ".md", ".srt", ".vtt", ".html", ".htm", ".pdf"}
MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".mp3", ".m4a", ".wav", ".aac", ".ogg", ".silk"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


class NeedsInput(Exception):
    pass


@dataclass
class Extraction:
    text: str
    method: str
    chunks: list[tuple[str, str]]
    title: str = ""
    publisher: str = ""


def normalize_url(value: str) -> tuple[str, str]:
    if not value.strip():
        return "", "note"
    parts = urlsplit(value.strip())
    if (parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443)
            or parts.hostname not in {"mp.weixin.qq.com", "weixin.qq.com"}):
        raise ValueError("只支持 HTTPS 公众号文章或视频号链接；其他内容请粘贴正文或上传文件。")
    kind = "article" if parts.hostname == "mp.weixin.qq.com" else "channels"
    if kind == "article" and not (parts.path == "/s" or parts.path.startswith("/s/")):
        raise ValueError("请提供公众号文章链接。")
    if kind == "channels" and not parts.path.startswith("/sph/"):
        raise ValueError("请提供视频号分享链接。")
    # Preserve WeChat identity and signatures; strip only the local fragment.
    return urlunsplit(("https", parts.hostname, parts.path, parts.query, "")), kind


def text_extraction(text: str, method="manual") -> Extraction:
    text = text.strip()
    if not text:
        raise NeedsInput("没有提取到可搜索的正文，请补充文字或字幕。")
    if len(text) > MAX_TEXT:
        raise NeedsInput("正文超过 100 万字符，请拆分为多个资料后导入。")
    chunks = []
    # Exact, non-overlapping source ranges keep locators reproducible.
    for offset in range(0, len(text), 900):
        chunks.append((f"字符 {offset + 1}–{min(offset + 900, len(text))}", text[offset:offset + 900]))
    return Extraction(text, method, chunks)


class ArticleParser(HTMLParser):
    def __init__(self, wechat=False):
        super().__init__(convert_charrefs=True)
        self.wechat = wechat
        self.stack = []
        self.skip = 0
        self.body = []
        self.title = []
        self.publisher = []
        self.found_body = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        parent = self.stack[-1][1] if self.stack else ""
        target = {"js_content": "body", "activity-name": "title", "js_name": "publisher"}.get(attrs.get("id"), parent)
        if not self.wechat and tag == "title":
            target = "title"
        if attrs.get("id") == "js_content":
            self.found_body = True
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        if tag in {"br", "p", "div", "section", "li", "h1", "h2", "h3"} and not self.skip:
            self.body.append("\n")
        if tag not in {"br", "img", "meta", "link", "hr", "input", "source"}:
            self.stack.append((tag, target))

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.skip = max(0, self.skip - 1)
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                break
        if tag in {"p", "div", "section", "li"}:
            self.body.append("\n")

    def handle_data(self, data):
        if self.skip:
            return
        target = self.stack[-1][1] if self.stack else ""
        if target == "title":
            self.title.append(data)
        elif target == "publisher":
            self.publisher.append(data)
        elif not self.wechat or target == "body":
            self.body.append(data)


def extract_html(raw: str, wechat=False) -> Extraction:
    parser = ArticleParser(wechat)
    parser.feed(raw)
    if wechat and not parser.found_body:
        raise NeedsInput("公众号页面未提供正文（可能需要验证或已失效）。请在微信打开后粘贴正文或上传保存的 HTML / PDF。")
    text = re.sub(r"\n[ \t]*\n+", "\n\n", "".join(parser.body)).strip()
    result = text_extraction(text, "wechat_article" if wechat else "html")
    result.title = "".join(parser.title).strip()[:300]
    result.publisher = "".join(parser.publisher).strip()[:200]
    return result


def public_addresses(host: str) -> list[str]:
    addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
        raise NeedsInput("目标地址不安全，已停止抓取。请改为上传正文。")
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    """Resolve once, connect to the validated IP, retain TLS hostname verification."""
    def __init__(self, hostname, address, timeout):
        super().__init__(hostname, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            if not ipaddress.ip_address(raw.getpeername()[0]).is_global:
                raise NeedsInput("目标地址不安全，已停止抓取。")
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def fetch_article(url: str) -> str:
    deadline = time.monotonic() + 30
    for _ in range(6):
        url, kind = normalize_url(url)
        if kind != "article":
            raise NeedsInput("只自动提取公众号文章；视频号请补充视频或字幕。")
        parts = urlsplit(url)
        address = public_addresses(parts.hostname)[0]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("article deadline")
        connection = PinnedHTTPS(parts.hostname, address, min(5, remaining))
        try:
            connection.request("GET", urlunsplit(("", "", parts.path, parts.query, "")),
                               headers={"User-Agent": "SignalDesk-Knowledge/1.0", "Accept": "text/html", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.getheader("Location", ""))
                continue
            if response.status != 200 or "text/html" not in response.getheader("Content-Type", "").lower():
                raise NeedsInput("公众号未返回可读取的文章，请补充正文、HTML 或 PDF。")
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise NeedsInput("页面返回不支持的压缩格式，请补充正文。")
            data = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("article deadline")
                connection.sock.settimeout(min(5, remaining)) if connection.sock else None
                block = response.read(65536)
                if not block:
                    break
                data.extend(block)
                if len(data) > 5 * 1024 * 1024:
                    raise NeedsInput("公众号页面过大，请保存正文后上传。")
            return data.decode("utf-8", errors="replace")
        finally:
            connection.close()
    raise NeedsInput("公众号跳转次数过多，请补充正文。")


def extract_subtitles(text: str) -> Extraction:
    chunks = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = block.strip().splitlines()
        index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if index is None:
            continue
        cue = re.sub(r"<[^>]+>", "", " ".join(lines[index + 1:])).strip()
        locator = lines[index].strip()[:100]
        if cue:
            for offset in range(0, len(cue), 900):
                chunks.append((locator, cue[offset:offset + 900]))
    if not chunks:
        raise NeedsInput("字幕没有可识别的时间轴，请检查 SRT / WebVTT 文件。")
    result = text_extraction("\n".join(f"[{loc}] {cue}" for loc, cue in chunks), "subtitle")
    result.chunks = chunks
    return result


def extract_file(path: Path, filename: str) -> Extraction:
    extension = Path(filename).suffix.lower()
    if extension in IMAGE_EXTENSIONS:
        raise NeedsInput("图片原件已保存。请在资料详情中明确同意云端 OCR，或手动补充正文。")
    if extension in MEDIA_EXTENSIONS:
        return transcribe_media(path)
    if path.stat().st_size > 20 * 1024 * 1024:
        raise NeedsInput("文字 / PDF 文件不能超过 20 MB，请拆分后导入。")
    if extension == ".pdf":
        from pypdf import PdfReader
        with path.open("rb") as stream:
            reader = PdfReader(stream)
            if reader.is_encrypted or len(reader.pages) > 500:
                raise NeedsInput("PDF 加密或超过 500 页，请解密或拆分后导入。")
            chunks = []
            total = 0
            for number, page in enumerate(reader.pages, 1):
                text = (page.extract_text() or "").strip()
                total += len(text)
                if total > MAX_TEXT:
                    raise NeedsInput("PDF 正文过长，请拆分后导入。")
                chunks.extend((f"第 {number} 页", text[offset:offset + 900]) for offset in range(0, len(text), 900))
        result = text_extraction("\n\n".join(text for _, text in chunks), "pdf_text")
        result.chunks = chunks
        return result
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError:
            raise NeedsInput("文件编码不可识别，请另存为 UTF-8 文本。") from None
    if extension in {".srt", ".vtt"}:
        return extract_subtitles(text)
    if extension in {".html", ".htm"}:
        return extract_html(text, wechat='id="js_content"' in text or "id='js_content'" in text)
    return text_extraction(text, "text_file")


def transcribe_media(path: Path) -> Extraction:
    settings = get_settings()
    if not settings.knowledge_asr_enabled or not settings.knowledge_asr_command:
        raise NeedsInput("视频 / 音频已保存。后台尚未启用语音识别，请上传 SRT / VTT 字幕或粘贴转写正文。")
    # Operator-controlled argument array, never a shell string or user-controlled executable.
    command = json.loads(settings.knowledge_asr_command)
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        raise NeedsInput("语音识别配置无效，请联系管理员；原始文件已保留。")
    with tempfile.TemporaryDirectory(prefix="signaldesk-asr-") as directory:
        output = Path(directory) / "transcript.json"
        subprocess.run([*command, str(path.resolve()), str(output)], shell=False, timeout=900,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not output.is_file() or output.stat().st_size > 5 * 1024 * 1024:
            raise NeedsInput("语音识别没有产生有效结果，请补充字幕。")
        payload = json.loads(output.read_text(encoding="utf-8"))
        segments = payload.get("segments", [])
        if not isinstance(segments, list) or len(segments) > 20000:
            raise NeedsInput("语音识别结果格式无效，请补充字幕。")
        chunks = []
        for segment in segments:
            start, end = float(segment["start"]), float(segment["end"])
            if not 0 <= start <= end <= 3600:
                raise NeedsInput("视频时长或时间轴超过限制（60 分钟）。")
            text = str(segment["text"]).strip()
            locator = f"{start:.1f}s–{end:.1f}s"
            if text:
                chunks.extend((locator, text[offset:offset + 900]) for offset in range(0, len(text), 900))
        result = text_extraction("\n".join(f"[{loc}] {cue}" for loc, cue in chunks), "asr")
        result.chunks = chunks
        return result
