"""配音阶段的公共部分：固定参数、密钥查找、HTTP、ffmpeg。

密钥只在内存里使用：任何地方都不打印、不写日志，只报告它的"来源"。
"""
import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal


def paid_max_tries():
    """Hermes supplies 1: an ambiguous paid response must not be resubmitted silently."""
    value = os.environ.get("OLDX_PAID_MAX_TRIES", "6").strip()
    if value not in {str(n) for n in range(1, 7)}:
        raise ValueError("OLDX_PAID_MAX_TRIES must be an integer from 1 to 6")
    return int(value)

# ---------------------------------------------------------------- 固定参数（用户已定，不再询问）
MODEL = "qwen-audio-3.1-tts-flash"
INSTRUCTION = "像朋友聊天一样自然随意的口语，不要播音腔，语速较快，句与句之间保留自然的停顿，语气轻松带笑意"
SAMPLE_RATE = 48000
MAX_CHARS = 1000         # 每段文字上限（实测：整篇 1551 字一次合成成功 3 次，488、501 字的段也稳；再长没测过）
WORKERS = 6              # 最高并发
SUBMIT_INTERVAL = 0.5    # 相邻两次提交的最小间隔（秒）。瞬间同时提交会被 429 限流，错开即可
GAP_SECONDS = 0.3        # 段与段之间的停顿
QUESTION_TAG = "[curious]"   # 问句（以问号结尾的句子）前加的情绪标签，只影响这一句；设为 "" 则不加。其他情绪标签一律不用
MAX_TRIES = paid_max_tries()

ASR_MODEL = "qwen-audio-3.1-asr-flash"       # 逐词时间戳 + 文字核对，都用它（不依赖本地识别工具）
ASR_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

# 项目缓存根目录：每份口播文案一个目录，配音、逐词时间、数字人等后续环节共用
WORKSPACE_ROOT = os.environ.get("DH_WORKSPACE", "").strip() or os.path.join(os.path.expanduser("~"), ".digital-human")

TTS_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
CLONE_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
KEY_PAGE = "https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key"
DEFAULT_KEY_FILE = os.path.join(WORKSPACE_ROOT if os.environ.get("OLDX_HERMES") else os.path.expanduser("~"), ".bailian", "api_key")

# 没有密钥时对用户说的话（原样输出）
GUIDE_TEXT = "本机没有注册百炼的APIkey。请获取apikey后，直接粘贴到对话输入框发送给我，其余的我来处理。\n获取地址：" + KEY_PAGE


# ---------------------------------------------------------------- 问句标签
_SENT = re.compile(r"[^。！？!?；;…~～\n]+[。！？!?；;…~～]*")


def tag_questions(text):
    """在每个以问号结尾的句子前加 QUESTION_TAG。只改发给合成接口的文字，不改文案和缓存里的原文。"""
    if not QUESTION_TAG:
        return text

    def f(m):
        s = m.group(0)
        if not re.search(r"[？?]", s[-3:]):
            return s
        lead = len(s) - len(s.lstrip())
        return s[:lead] + QUESTION_TAG + s[lead:]
    return _SENT.sub(f, text)


def tag_emotions(text, rules):
    """按项目里 emotions.json 的规则，在指定的句子或分句前加情绪标签（如 [excited]）。
    rules: [{"at": "这句话开头的几个字", "tag": "[excited]", "n": 第几次出现（默认 1）}]。
    只改发给合成接口的文字；找不到 at 的规则跳过（这一段里没有这句话）。那个位置已经有问句标签时不再加。"""
    spots = []
    for r in rules or []:
        at, tag, n = r.get("at", ""), r.get("tag", ""), int(r.get("n", 1))
        if not at or not tag:
            continue
        i = -1
        for _ in range(n):
            i = text.find(at, i + 1)
            if i < 0:
                break
        if i < 0 or (QUESTION_TAG and text[max(0, i - len(QUESTION_TAG)):i] == QUESTION_TAG):
            continue
        spots.append((i, tag))
    for i, tag in sorted(spots, reverse=True):
        text = text[:i] + tag + text[i:]
    return text


def apply_carry(paras, rules, carry="auto", start_tag="[excited]", gap=80):
    """给每一段加好所有标签，返回和 paras 等长的列表。
    先按 rules 在指定的句子前加情绪标签、给问句加问句标签；然后“沿用”：没有自己标签的句子，沿用前面最近一个情绪标签，
    这样信息密集的地方（报档位、念清单）和段与段的交界都不会出现情绪突变。第一句之前没有标签时用 start_tag。
    carry: "auto"（默认）段首必补，段内某个句子没有标签、而且离上一个标签已经超过 gap 个字时才补，既不留长长一截没有标签的话，也不让标签太碎；
           "sentence" 每个没标签的句子都补（标签最密）；"paragraph" 只在段首补；"off" 不补。问句标签不算情绪，也不会被沿用；问句后面的那一句总是补沿用的情绪标签。
    注意每一段是单独一次合成请求，标签不会跨段生效，所以段首的沿用是必须的。"""
    out, cur = [], start_tag
    for p in paras:
        tagged = tag_emotions(tag_questions(p), rules)
        parts = _SENT.findall(tagged)
        if not rules or carry == "off" or "".join(parts) != tagged:
            out.append(tagged)
            continue
        res, since, after_q = [], 0, False
        for k, sent in enumerate(parts):
            body = sent.lstrip()
            m = re.match(r"\[[a-z_]+\]", body)
            if m:
                after_q = m.group(0) == QUESTION_TAG
                if not after_q:
                    cur = m.group(0)
                res.append(sent)
                since = len(body)
            elif k == 0 or after_q or carry == "sentence" or (carry == "auto" and since >= gap):
                res.append(sent[:len(sent) - len(body)] + cur + body)     # 问句标签只管问句那一句，后面马上接回原来的情绪
                since, after_q = len(body), False
            else:
                res.append(sent)
                since += len(body)
        out.append("".join(res))
    return out


def tags_not_at_sentence_start(tagged):
    """标签不在句首的位置（句中标签容易被念出来，也会让一句话内部变调）。"""
    bad = []
    for m in re.finditer(r"\[[a-z_]+\]", tagged):
        i = m.start()
        before = tagged[:i].rstrip()
        if before and before[-1] not in "。！？!?；;…]":
            bad.append(tagged[max(0, i - 8):i + 12])
    return bad


# ---------------------------------------------------------------- 密钥
def find_key():
    """返回 (key, 来源描述)；找不到返回 (None, None)。来源描述不含密钥。"""
    k = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if k:
        return k, "环境变量 DASHSCOPE_API_KEY"
    f = os.environ.get("BL_KEY_FILE", "").strip()
    if f and os.path.isfile(f):
        k = open(f, encoding="utf-8").read().strip()
        if k:
            return k, "BL_KEY_FILE 指向的文件"
    if os.path.isfile(DEFAULT_KEY_FILE):
        k = open(DEFAULT_KEY_FILE, encoding="utf-8").read().strip()
        if k:
            return k, "默认密钥文件 " + DEFAULT_KEY_FILE
    return None, None


def require_key():
    key, src = find_key()
    if not key:
        print(GUIDE_TEXT)
        sys.exit(3)
    return key


# ---------------------------------------------------------------- HTTP
# Official Beijing list prices checked 2026-10-22. No free quota or discounts assumed.
# https://help.aliyun.com/zh/model-studio/qwen-audio-3-1-tts-flash
# https://help.aliyun.com/zh/model-studio/qwen-audio-3-1-asr-flash
VOICE_TOKEN_PRICES = {
    "qwen-audio-3.1-tts-flash": ("tts", Decimal("1.5"), Decimal("12")),
    "qwen-audio-3.1-asr-flash": ("asr", Decimal("0.8"), Decimal("2.7")),
}
_usage_lock = threading.Lock()
_paid_request_lock = threading.Lock()


def _usage_path():
    path = os.environ.get("OLDX_VOICE_USAGE_LEDGER", "").strip()
    if path and not os.path.isabs(path):
        raise ValueError("OLDX_VOICE_USAGE_LEDGER must be an absolute private file path")
    return path


def _write_voice_usage(path, model, call_id, event, status=None, data=None):
    """Append only billing metadata. Never write request text, audio, keys or errors.

    A started row is unknown until its finished row arrives. Consumers keep the last
    row for each call_id, so a killed process or missing response cannot look free.
    Writes are serialized within this process (the voice workers share this lock).
    """
    if not path:
        return
    operation, input_price, output_price = VOICE_TOKEN_PRICES[model]
    raw = data.get("usage") if isinstance(data, dict) else None
    usage = {name: raw[name] for name in ("input_tokens", "output_tokens", "total_tokens")
             if isinstance(raw, dict) and type(raw.get(name)) is int and raw[name] >= 0}
    measured = "input_tokens" in usage and "output_tokens" in usage
    cost = ((Decimal(usage["input_tokens"]) * input_price + Decimal(usage["output_tokens"]) * output_price)
            / Decimal(1000000)) if measured else None
    request_id = data.get("request_id") if isinstance(data, dict) else None
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id):
        request_id = None
    row = {"schema": "oldx-voice-usage/v1", "call_id": call_id, "event": event,
           "at": datetime.now(timezone.utc).isoformat(), "model": model, "operation": operation,
           "region": "cn-beijing", "currency": "CNY", "request_id": request_id,
           "http_status": status, "state": "measured" if measured else "unknown", "usage": usage,
           "cost_cny": format(cost, "f") if cost is not None else None,
           "input_per_million_cny": str(input_price), "output_per_million_cny": str(output_price),
           "price_basis": "official_list_price", "price_checked_at": "2026-10-22"}
    line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _usage_lock:
        # Reject links even if the target happens to be writable. The caller owns
        # creation of the private job directory; this helper does not create it.
        probe = os.path.abspath(path)
        while True:
            if os.path.islink(probe) or (hasattr(os.path, "isjunction") and os.path.isjunction(probe)):
                raise ValueError("Voice usage ledger cannot use a linked path")
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())


def _check_voice_budget(path, limit):
    """Soft stop between requests; one in-flight provider request can exceed it."""
    if not path:
        raise ValueError("A voice budget requires OLDX_VOICE_USAGE_LEDGER")
    try:
        maximum = Decimal(limit)
        if not maximum.is_finite() or maximum <= 0:
            raise ValueError()
    except (ValueError, ArithmeticError):
        raise ValueError("OLDX_VOICE_BUDGET_CNY must be a positive finite CNY amount") from None
    latest = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as stream:
                for line in stream:
                    row = json.loads(line)
                    if row.get("schema") != "oldx-voice-usage/v1" or not row.get("call_id"):
                        raise ValueError()
                    latest[row["call_id"]] = row
        except (ValueError, AttributeError, TypeError):
            raise RuntimeError("Voice billing ledger is incomplete; confirm prior charges before retrying") from None
    total = Decimal(0)
    for row in latest.values():
        if row.get("state") != "measured" or row.get("cost_cny") is None:
            raise RuntimeError("A previous voice request has unknown cost; confirm its result before retrying")
        try:
            cost = Decimal(str(row["cost_cny"]))
            if not cost.is_finite() or cost < 0:
                raise ValueError()
        except (ValueError, ArithmeticError):
            raise RuntimeError("Voice billing ledger has invalid cost; confirm prior charges before retrying") from None
        total += cost
    if total >= maximum:
        raise RuntimeError("Voice budget reached; confirm an additional budget before another paid request")


def post_json(url, body, key, timeout=180):
    """Serialize budgeted voice calls so cleanup/alignment share the same soft stop."""
    budget = os.environ.get("OLDX_VOICE_BUDGET_CNY", "").strip()
    if body.get("model") in VOICE_TOKEN_PRICES and budget:
        with _paid_request_lock:
            _check_voice_budget(_usage_path(), budget)
            return _post_json(url, body, key, timeout)
    return _post_json(url, body, key, timeout)


def _post_json(url, body, key, timeout=180):
    """返回 (status, data)。status=0 表示结果未知；Hermes 禁止自动重发。"""
    model = body.get("model")
    ledger = _usage_path() if model in VOICE_TOKEN_PRICES else ""
    call_id = uuid.uuid4().hex
    req = urllib.request.Request(
        url, json.dumps(body, ensure_ascii=False).encode("utf-8"),
        {"Content-Type": "application/json", "Authorization": "Bearer " + key})
    # Fail before dispatch if billing evidence cannot be persisted.
    _write_voice_usage(ledger, model, call_id, "started")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, data = r.status, json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            status, data = e.code, json.loads(raw)
        except ValueError:
            status, data = e.code, {"message": raw[:300]}
    except Exception as e:                      # 断连、超时等
        status, data = 0, {"message": type(e).__name__}
    # Do not put this in the network try: a ledger failure after dispatch must
    # raise, leaving its started/unknown row, rather than trigger a paid retry.
    billing_data = data
    if isinstance(data, dict) and isinstance(data.get("request_id"), str) and key and key in data["request_id"]:
        billing_data = {"usage": data.get("usage")}
    _write_voice_usage(ledger, model, call_id, "finished", status, billing_data)
    return status, data


def download(url, dst, tries=3):
    last = None
    for _ in range(tries):
        try:
            urllib.request.urlretrieve(url, dst)
            return
        except Exception as e:
            last = e
    raise RuntimeError("下载音频失败：" + type(last).__name__)


# ---------------------------------------------------------------- 项目缓存
def update_meta(workdir, **kw):
    """如果 workdir 是项目目录（有 meta.json），把进度写回去；否则什么也不做。"""
    p = os.path.join(workdir, "meta.json")
    if not os.path.isfile(p):
        return
    m = json.load(open(p, encoding="utf-8"))
    m.update(kw)
    json.dump(m, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- ffmpeg（实现在 scripts/_util.py，这里保留原来的名字）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _util  # noqa: E402


def ffmpeg():
    return _util.ffmpeg_exe()


def to_pcm_wav(src, dst, sr=SAMPLE_RATE):
    """统一转成 16bit 单声道 wav，顺便修正接口返回的 wav 头里不准的时长字段。"""
    subprocess.run([ffmpeg(), "-y", "-v", "error", "-i", src, "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", dst],
                   check=True)


def media_seconds(path):
    """用 ffmpeg 读时长（秒），读不到返回 None。"""
    return _util.media_seconds(path)
