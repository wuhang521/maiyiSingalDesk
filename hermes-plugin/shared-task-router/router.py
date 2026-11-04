from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
PLATFORM_ALIASES = {"qqbot": "qq", "qq": "qq", "weixin": "weixin", "wecom": "wecom"}
DEFAULT_STAFF_ALIASES = {
    "杰哥": "杰哥",
    "靓仔": "杰哥",
    "敏总": "敏总",
    "姐": "敏总",
    "振华": "振华",
}
DEFAULT_CREATOR_BY_IDENTITY = {
    "qq:杰哥": "靓仔",
    "weixin:敏总": "姐",
    "wecom:振华": "振华",
}

CALENDAR_PATTERNS = (
    "添加到日历",
    "加到日历",
    "同步到日历",
    "创建日历事件",
    "生成ics",
    "生成 ics",
    "导出ics",
    "导出 ics",
    "从日历删除",
)
META_PATTERNS = (
    "为什么",
    "为啥",
    "怎么识别",
    "识别错",
    "识别了",
    "误识别",
    "报错",
    "什么问题",
    "不应该",
)
COMPLETE_WORDS = ("完成", "做完", "干完", "搞定", "办完")
DELETE_WORDS = ("删除", "删掉", "删了", "移到回收站")
RESTORE_WORDS = ("恢复", "还原", "从回收站恢复")
REOPEN_WORDS = ("重新打开", "重新开始", "重做", "撤销完成")
DETAIL_WORDS = ("详情", "详细", "具体要求", "是做什么", "什么内容")
UPDATE_WORDS = ("修改", "改成", "改到", "延期", "推迟", "转给", "改给")

TASK_NO_RE = re.compile(r"(?i)T\s*0*([1-9]\d*)")
NUMBERED_ITEM_RE = re.compile(
    r"(?:^|[\r\n]|\s)(?:\d{1,2}|[一二三四五六七八九十]{1,3})[.、)）]\s*"
)


@dataclass(frozen=True)
class StaffIdentity:
    id: int
    name: str
    channels: tuple[dict[str, Any], ...] = ()
    enabled: bool = True


@dataclass(frozen=True)
class TimeParse:
    value: datetime | None = None
    matched: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class Intent:
    kind: str
    assignee: str | None = None
    task_no: str | None = None
    title: str | None = None
    content: str = ""
    due_at: datetime | None = None
    state: str = "active"
    message: str | None = None
    new_assignee: str | None = None
    rule_id: str = ""


@dataclass(frozen=True)
class PreparedRoute:
    intents: tuple[Intent, ...]
    staff: tuple[StaffIdentity, ...]
    platform: str
    self_name: str | None
    recipient_id: str | None
    creator: str
    message_id: str | None


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.replace("\u200b", "").replace("\xa0", " ")
    if text.startswith("[Quoted message]:"):
        parts = re.split(r"(?:\r?\n){2,}", text, maxsplit=1)
        text = parts[1] if len(parts) == 2 else ""
    text = re.sub(r"<@!?[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def normalize_platform(value: str) -> str:
    return PLATFORM_ALIASES.get((value or "").lower(), (value or "").lower())


def is_complete_all_command(value: str) -> bool:
    clean = normalize_text(value).strip("。.!！ ")
    complete = r"(?:完成|做完|干完|搞定|办完)"
    return bool(
        re.fullmatch(
            rf"(?:我(?:的)?\s*)?(?:"
            rf"(?:已经|已)?(?:把\s*)?(?:全部|所有)(?:的)?(?:任务|待办)(?:都|全都|全|已经|已)?{complete}(?:了)?|"
            rf"(?:已经|已)?(?:全部|全都){complete}(?:了)?(?:任务|待办)?|"
            rf"(?:任务|待办)(?:都|全都|全|全部|已经|已){complete}(?:了)?|"
            rf"(?:已经|已)?{complete}(?:了)?(?:全部|所有)(?:的)?(?:任务|待办)|"
            rf"(?:都|全都|全){complete}(?:了)?(?:任务|待办)?"
            rf")",
            clean,
        )
    )


def chinese_number(value: str) -> int | None:
    value = normalize_text(value)
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value in digits:
        return digits[value]
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        units = digits.get(right, 0) if right else 0
        return tens * 10 + units
    return None


def _weekday_delta(prefix: str, target: int, now: datetime) -> int:
    current = now.weekday()
    if prefix == "下下周":
        return 14 - current + target
    if prefix == "下周":
        return 7 - current + target
    if prefix in {"本周", "这周"}:
        return target - current
    delta = (target - current) % 7
    return delta


def parse_chinese_datetime(text: str, now: datetime | None = None) -> TimeParse:
    text = normalize_text(text)
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    matches: list[str] = []
    date_value = None

    duration_number = r"(?:\d+(?:\.\d+)?|半|[零〇一二两三四五六七八九十]{1,3})"

    def duration_value(raw: str | None) -> float:
        if not raw:
            return 0.0
        if raw == "半":
            return 0.5
        if re.fullmatch(r"\d+(?:\.\d+)?", raw):
            return float(raw)
        value = chinese_number(raw)
        return float(value) if value is not None else -1.0

    duration_entries: list[tuple[re.Match[str], timedelta]] = []
    occupied: list[tuple[int, int]] = []
    for item in re.finditer(
        rf"(?<![\d.])(?P<base>{duration_number})\s*(?:个)?半小时后",
        text,
    ):
        base = duration_value(item.group("base"))
        duration_entries.append((item, timedelta(hours=base + 0.5)))
        occupied.append(item.span())

    composite_pattern = re.compile(
        rf"(?<![\d.个])(?=(?:{duration_number})(?:天|(?:个)?小时|分钟))"
        rf"(?:(?P<days>{duration_number})天)?"
        rf"(?:(?P<hours>{duration_number})(?:个)?小时)?"
        rf"(?:(?P<minutes>{duration_number})分钟)?后"
    )
    for item in composite_pattern.finditer(text):
        if any(item.start() < end and item.end() > start for start, end in occupied):
            continue
        days = duration_value(item.group("days"))
        hours = duration_value(item.group("hours"))
        minutes = duration_value(item.group("minutes"))
        if min(days, hours, minutes) < 0:
            return TimeParse(error="相对时间不合法，请重新说明。")
        duration_entries.append(
            (item, timedelta(days=days, hours=hours, minutes=minutes))
        )
        occupied.append(item.span())

    for item in re.finditer(
        rf"(?<![\d.])(?P<weeks>{duration_number})\s*(?:个)?(?:周|星期)后",
        text,
    ):
        if any(item.start() < end and item.end() > start for start, end in occupied):
            continue
        weeks = duration_value(item.group("weeks"))
        duration_entries.append((item, timedelta(days=weeks * 7)))
        occupied.append(item.span())

    if len(duration_entries) > 1:
        return TimeParse(error="检测到多个相对时间，请只保留一个时间。")
    duration_entry = duration_entries[0] if duration_entries else None
    if duration_entry:
        duration, delta = duration_entry
        remainder = f"{text[:duration.start()]} {text[duration.end():]}"
        if re.search(
            r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|"
            r"(?<!\d)\d{1,2}/\d{1,2}(?!\d)|"
            r"\d{1,2}月\d{1,2}[日号]?|大后天|后天|明天|明日|今天|今日|"
            r"(?:下下周|下周|本周|这周|周|星期|礼拜)[一二三四五六日天]|"
            r"凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|今晚|明早|明晚|"
            r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)|"
            r"[零〇一二两三四五六七八九十\d]{1,3}点",
            remainder,
        ):
            return TimeParse(error="检测到相对时间和具体时间混用，请只保留一个时间。")
        if delta <= timedelta(0):
            return TimeParse(error="相对时间不合法，请重新说明。")
        return TimeParse(
            value=(now + delta).replace(second=0, microsecond=0),
            matched=(duration.group(0),),
        )
    if re.search(
        rf"{duration_number}\s*(?:个)?(?:月|季度|年)后",
        text,
    ):
        return TimeParse(error="暂不支持按月、季度或年推算，请改成明确日期和时间。")
    if re.search(r"(?:刻钟|小时|分钟|天|周|星期|月|季度|年).{0,4}后", text):
        return TimeParse(error="相对时间表达不明确，请改成明确的小时、分钟或日期时间。")

    iso_dates = list(
        re.finditer(r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)", text)
    )
    explicit_dates = list(re.finditer(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]?", text))
    relative_dates = list(re.finditer(r"(大后天|后天|明天|明日|今天|今日)", text))
    weekday_dates = list(
        re.finditer(r"(下下周|下周|本周|这周|周|星期|礼拜)([一二三四五六日天])", text)
    )
    slash_dates = list(
        re.finditer(
            r"(?<![\d/-])(\d{1,2})[/-](\d{1,2})"
            r"(?=$|[日号]|\s*(?:上午|下午|中午|晚上|凌晨|早上|傍晚|"
            r"(?:[01]?\d|2[0-3]):[0-5]\d))",
            text,
        )
    )
    date_groups = [
        group
        for group in (iso_dates, explicit_dates, relative_dates, weekday_dates, slash_dates)
        if group
    ]
    if len(date_groups) > 1 or any(len(group) > 1 for group in date_groups):
        return TimeParse(error="检测到多个日期，请只保留一个明确日期。")

    iso_date = iso_dates[0] if iso_dates else None
    explicit_date = explicit_dates[0] if explicit_dates else None
    if iso_date:
        year, month, day = map(int, iso_date.groups())
        try:
            candidate = datetime(year, month, day, tzinfo=SHANGHAI)
        except ValueError:
            return TimeParse(error="日期不合法，请重新说明日期。")
        date_value = candidate.date()
        matches.append(iso_date.group(0))
    elif explicit_date:
        year = int(explicit_date.group(1) or now.year)
        month, day = int(explicit_date.group(2)), int(explicit_date.group(3))
        try:
            candidate = datetime(year, month, day, tzinfo=SHANGHAI)
        except ValueError:
            return TimeParse(error="日期不合法，请重新说明日期。")
        if explicit_date.group(1) is None and candidate.date() < now.date():
            candidate = candidate.replace(year=now.year + 1)
        date_value = candidate.date()
        matches.append(explicit_date.group(0))
    elif relative_dates:
        relative = relative_dates[0]
        offset = {
            "今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2, "大后天": 3
        }[relative.group(1)]
        date_value = (now + timedelta(days=offset)).date()
        matches.append(relative.group(0))
    elif weekday_dates:
        weekday = weekday_dates[0]
        target = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4,
                  "六": 5, "日": 6, "天": 6}[weekday.group(2)]
        prefix = weekday.group(1)
        delta = _weekday_delta(prefix, target, now)
        if prefix in {"本周", "这周"} and delta < 0:
            return TimeParse(error=f"{weekday.group(0)}已经过去，请明确说下周几。")
        date_value = (now + timedelta(days=delta)).date()
        matches.append(weekday.group(0))
    elif slash_dates:
        if len(slash_dates) > 1:
            return TimeParse(error="检测到多个日期，请只保留一个明确日期。")
        slash_date = slash_dates[0]
        month, day = int(slash_date.group(1)), int(slash_date.group(2))
        try:
            candidate = datetime(now.year, month, day, tzinfo=SHANGHAI)
        except ValueError:
            return TimeParse(error="日期不合法，请重新说明日期。")
        if candidate.date() < now.date():
            candidate = candidate.replace(year=now.year + 1)
        date_value = candidate.date()
        matches.append(slash_date.group(0))
    period_matches = list(
        re.finditer(r"(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|今晚|明早|明晚)", text)
    )
    if len(period_matches) > 1:
        return TimeParse(error="检测到多个时间段，请只保留一个明确时间。")
    period_match = period_matches[0] if period_matches else None
    period = period_match.group(1) if period_match else ""
    if period_match:
        matches.append(period_match.group(0))
        if period == "今晚" and date_value is None:
            date_value = now.date()
        elif period in {"明早", "明晚"} and date_value is None:
            date_value = (now + timedelta(days=1)).date()

    if re.search(
        r"[零〇一二两三四五六七八九十\d]{1,3}(?:点)?\s*[-~—至到]\s*"
        r"[零〇一二两三四五六七八九十\d]{1,3}点",
        text,
    ):
        return TimeParse(error="检测到时间区间，请明确一个具体截止时间。")
    quarter_mentions = re.findall(
        r"点\s*([零〇一二两三四五六七八九十\d]{1,3})刻",
        text,
    )
    if any(item not in {"一", "两", "三"} for item in quarter_mentions):
        return TimeParse(error="刻钟表达不明确，请改用 15、30 或 45 分钟。")

    hour = minute = None
    colon_times = list(
        re.finditer(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", text)
    )
    point_times = list(
        re.finditer(
            r"([零〇一二两三四五六七八九十\d]{1,3})点"
            r"(?:(半)|(一刻|两刻|三刻)|([零〇一二两三四五六七八九十\d]{1,3})分?)?",
            text,
        )
    )
    if len(colon_times) + len(point_times) > 1:
        return TimeParse(error="检测到多个具体时间，请只保留一个时间。")
    colon_time = colon_times[0] if colon_times else None
    point_time = point_times[0] if point_times else None
    if colon_time:
        hour, minute = int(colon_time.group(1)), int(colon_time.group(2))
        matches.append(colon_time.group(0))
        if not period and 1 <= hour <= 12 and not colon_time.group(1).startswith("0"):
            return TimeParse(error="请说明是上午还是下午，或使用 14:00 这样的 24 小时格式。")
    elif point_time:
        hour = chinese_number(point_time.group(1))
        if point_time.group(2):
            minute = 30
        elif point_time.group(3):
            minute = {"一刻": 15, "两刻": 30, "三刻": 45}[point_time.group(3)]
        else:
            minute = chinese_number(point_time.group(4) or "0")
        if hour is None or minute is None or hour > 23 or minute > 59:
            return TimeParse(error="时间不合法，请重新说明几点。")
        matches.append(point_time.group(0))
        if not period and 1 <= hour <= 12:
            return TimeParse(error="请说明是上午还是下午。")

    if hour is None:
        if matches or date_value is not None:
            return TimeParse(error="已识别到日期或时间段，但缺少具体几点，请补充明确时间。")
        return TimeParse(value=None, matched=tuple(dict.fromkeys(matches)))

    if period in {"下午", "傍晚", "晚上", "今晚", "明晚"} and hour < 12:
        hour += 12
    elif period in {"晚上", "今晚", "明晚"} and hour == 12:
        hour = 0
        if date_value is None:
            date_value = now.date()
        date_value += timedelta(days=1)
    elif period == "中午" and hour < 11:
        hour += 12
    elif period in {"凌晨", "早上", "早晨", "上午", "明早"} and hour == 12:
        hour = 0

    if date_value is None:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        date_value = candidate.date()

    value = datetime.combine(date_value, datetime.min.time(), tzinfo=SHANGHAI)
    value = value.replace(hour=hour, minute=minute)
    if value < now and any(item.startswith(("本周", "这周")) for item in matches):
        return TimeParse(error="这个时间已经过去，请重新说明日期。")
    if value < now and any(item in {"今天", "今日"} for item in matches):
        return TimeParse(error="这个时间已经过去，请重新说明时间。")
    if value < now and any("月" in item or "/" in item or "-" in item for item in matches):
        return TimeParse(error="这个时间已经过去，请重新说明日期。")
    if value <= now and period == "今晚":
        return TimeParse(error="今晚这个时间已经过去，请重新说明时间。")
    if value <= now and date_value == now.date() and not any(
        item in {"今天", "今日"} for item in matches
    ):
        value += timedelta(days=7 if any("周" in item or "星期" in item or "礼拜" in item for item in matches) else 1)
    return TimeParse(value=value, matched=tuple(dict.fromkeys(matches)))


def _remove_time_phrases(text: str, parsed: TimeParse) -> str:
    result = text
    for item in sorted(parsed.matched, key=len, reverse=True):
        result = result.replace(item, " ")
    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"^[在于到的,，:：、\s]+|[。.!！\s]+$", "", result)
    return result.strip()


def _alias_map(staff: Iterable[StaffIdentity], configured: dict[str, str] | None = None) -> dict[str, str]:
    names = {item.name for item in staff if item.enabled}
    result = {name: name for name in names}
    for alias, canonical in DEFAULT_STAFF_ALIASES.items():
        if canonical in names:
            result[alias] = canonical
    for alias, canonical in (configured or {}).items():
        if canonical in names:
            result[str(alias).strip()] = canonical
    return {key: value for key, value in result.items() if key}


def _alias_pattern(aliases: dict[str, str]) -> str:
    patterns = []
    for item in sorted(aliases, key=len, reverse=True):
        escaped = re.escape(item)
        if item == "姐":
            escaped += r"(?!姐|夫|妹|弟|们)"
        elif item == "敏总":
            escaped += r"(?!监|经理|裁|管|助理|秘书|们)"
        elif item == "杰哥":
            escaped += r"(?!们|哥|姐|弟|妹)"
        elif item == "靓仔":
            escaped += r"(?!们)"
        patterns.append(escaped)
    return "|".join(patterns)


def _normalize_task_no(text: str) -> str | None:
    match = TASK_NO_RE.search(text)
    return f"T{int(match.group(1))}" if match else None


def _contains_any(text: str, words: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def _calendar_requested(text: str) -> bool:
    lowered = normalize_text(text).lower()
    return any(item in lowered for item in CALENDAR_PATTERNS)


def _looks_meta(text: str) -> bool:
    clean = normalize_text(text)
    numbered = list(NUMBERED_ITEM_RE.finditer(clean))
    if len(numbered) >= 2:
        header = clean[: numbered[0].start()]
        if re.search(r"测试|示例|演示|模拟|指令|是否执行|会不会执行|是否创建", header):
            return True
    if re.match(
        r"^(?:这是|这只是|这只是一个|我想|我在|帮我看看|请判断|"
        r"以下|下面|仅演示|仅测试)",
        clean,
    ) and re.search(r"测试|示例|演示|模拟|指令|会不会执行|是否执行|创建任务", clean):
        return True
    if re.match(
        r"^(?:我说|比如|例如|举例|假如|假设|测试一下|测试下|"
        r"测试|示例|演示|模拟|试试看)\s*[:：]?",
        clean,
    ):
        return True
    if re.match(r"^(?:以下|下面).*(?:测试|示例|演示|模拟|指令)", clean):
        return True
    if re.match(r"^(?:请)?判断.*(?:指令|消息).*(?:是否|会不会).*(?:创建|生成|任务)", clean):
        return True
    if re.search(
        r"不要.*(?:执行|创建)|无需执行|仅供测试|仅作测试|"
        r"仅供演示|仅作演示|只是示例|只作示例",
        clean,
    ):
        return True
    if re.match(
        r"^(?:为什么|为啥|怎么|如何|能不能|是否).*(?:任务|待办|提醒|识别|安排)",
        clean,
    ):
        return True
    return bool(re.search(r"怎么识别|识别错|误识别|识别成|不应该.*(?:任务|提醒)", clean))


def _looks_question(text: str) -> bool:
    clean = normalize_text(text).strip("。.!！ ")
    if not clean:
        return False
    if clean.endswith(
        (
            "吗", "么", "呢", "没", "没有", "是不是", "对不对",
            "可不可以", "行不行", "好不好", "能不能", "要不要", "有没有",
            "了吧", "是吧", "可以吧", "吧", "嘛", "呗", "?", "？",
        )
    ):
        return True
    if re.search(
        r"是不是|是否|为什么|为啥|有没有|怎么|什么时候|何时|"
        r"可不可以|该不该|应不应该|能否|可否|行不行|好不好|"
        r"(?:和|跟|与)谁|谁(?:来|去|负责)?|哪个|哪些|哪位|啥|多少|"
        r"几(?:点|个|项|人|次)|还是|或者",
        clean,
    ):
        return True
    if re.match(
        r"^(?:几点|什么时候|何时|为什么|为何|怎么|谁|哪个|哪些|"
        r"什么(?:内容|任务|标题|时候)?|是否|有没有|能不能)",
        clean,
    ):
        return True
    return bool(
        re.search(
            r"(?:今天|明天|后天|大后天|周[一二三四五六日天]|星期[一二三四五六日天])?"
            r"\s*(?:几点|什么时候|何时)",
            clean,
        )
    )


def _looks_negative_instruction(text: str) -> bool:
    clean = normalize_text(text)
    return bool(
        re.search(
            r"不|没|未|别|勿|取消|撤销|作废|停止|暂停|禁止|不得",
            clean,
        )
    )


def _multiple_assignees(
    text: str,
    aliases: dict[str, str],
    self_name: str | None = None,
) -> tuple[str, ...]:
    if not aliases:
        return ()
    alias_re = _alias_pattern(aliases)
    names: list[str] = []
    connector = (
        r"(?:和|与|及|跟|以及|还有|同|、\s*(?:还有)?|"
        r"或者|还是|或|并(?:让|叫|通知|给)?)"
    )
    for match in re.finditer(
        rf"({alias_re}|我)\s*{connector}\s*({alias_re}|我)",
        normalize_text(text),
    ):
        for raw in (match.group(1), match.group(2)):
            names.append((self_name or "__self__") if raw == "我" else aliases[raw])
    titled_person = r"[\u4e00-\u9fffA-Za-z0-9_-]{1,8}(?:总|哥|姐|经理|主管|主任|老师|先生|女士|老板|工)"
    for match in re.finditer(
        rf"({alias_re}|我)\s*{connector}\s*({titled_person})",
        normalize_text(text),
    ):
        first = match.group(1)
        names.extend(
            (
                (self_name or "__self__") if first == "我" else aliases[first],
                f"__unknown__:{match.group(2)}",
            )
        )
    for match in re.finditer(
        rf"(?:转给|改给)\s*({alias_re}|我)",
        normalize_text(text),
    ):
        raw = match.group(1)
        names.append((self_name or "__self__") if raw == "我" else aliases[raw])
    return tuple(dict.fromkeys(names)) if len(set(names)) > 1 else ()


def _task_number_intent(
    text: str,
    aliases: dict[str, str],
    self_name: str | None,
    now: datetime,
) -> Intent | None:
    clean = normalize_text(text).strip("。.!！ ")
    task_nos = [f"T{int(item)}" for item in TASK_NO_RE.findall(clean)]
    if not task_nos:
        return None
    distinct = list(dict.fromkeys(task_nos))
    if len(distinct) > 1:
        return Intent(
            kind="clarify",
            message="一次请只操作一个任务编号，避免改错任务。",
            rule_id="MULTIPLE_TASK_COMMANDS",
        )
    task_no = distinct[0]

    if _multiple_assignees(clean, aliases, self_name):
        return Intent(
            kind="clarify",
            task_no=task_no,
            message="一次只能给一个负责人，请只保留一个姓名。",
            rule_id="UPDATE_MULTIPLE_ASSIGNEES",
        )
    negative_or_question = _looks_question(clean) or _looks_negative_instruction(clean)
    if negative_or_question:
        return Intent(kind="detail", task_no=task_no, rule_id="TASK_STATUS_QUESTION")

    token = r"(?i:T\s*0*[1-9]\d*)"
    polite = r"(?:请|麻烦|帮我|帮忙)?\s*"
    restore_command = bool(
        re.fullmatch(rf"{polite}(?:恢复|还原|从回收站恢复)\s*(?:任务)?\s*{token}", clean)
        or re.fullmatch(rf"{token}\s*(?:恢复|还原)(?:了)?", clean)
    )
    reopen_command = bool(
        re.fullmatch(rf"{polite}(?:重新打开|重新开始|重做|撤销完成)\s*(?:任务)?\s*{token}", clean)
        or re.fullmatch(rf"{token}\s*(?:重新打开|重新开始|重做)(?:了)?", clean)
    )
    delete_command = bool(
        re.fullmatch(rf"{polite}(?:删除|删掉|移到回收站)\s*(?:任务)?\s*{token}", clean)
        or re.fullmatch(rf"{token}\s*(?:删除|删掉|移到回收站)(?:了)?", clean)
    )
    complete_command = bool(
        re.fullmatch(
            rf"{polite}(?:把\s*)?(?:任务)?\s*{token}\s*(?:标记为)?"
            rf"(?:已经|已)?(?:完成|做完|干完|搞定|办完)(?:了)?",
            clean,
        )
        or re.fullmatch(
            rf"{polite}(?:完成|做完|干完|搞定|办完)\s*(?:任务)?\s*{token}",
            clean,
        )
    )
    update_command = bool(
        re.fullmatch(rf"{polite}(?:把\s*)?{token}\s*(?:修改|改成|改到|延期|推迟|转给|改给).+", clean)
        or re.fullmatch(rf"{polite}(?:修改|更新)\s*{token}\s*.+", clean)
    )
    command_count = sum(
        (restore_command, reopen_command, delete_command, complete_command, update_command)
    )
    if command_count > 1:
        return Intent(
            kind="clarify",
            message="一次请只执行一种任务操作，避免改错任务。",
            rule_id="MULTIPLE_TASK_COMMANDS",
        )
    if restore_command:
        return Intent(kind="restore", task_no=task_no, rule_id="RESTORE_BY_NO")
    if reopen_command:
        return Intent(kind="reopen", task_no=task_no, rule_id="REOPEN_BY_NO")
    if delete_command:
        return Intent(kind="delete", task_no=task_no, rule_id="DELETE_BY_NO")
    if complete_command:
        return Intent(kind="complete", task_no=task_no, rule_id="COMPLETE_BY_NO")
    detail_command = bool(
        re.fullmatch(
            rf"{polite}(?:详情|详细内容|具体要求|查看|看看|查询)?\s*"
            rf"(?:任务)?\s*{token}\s*(?:的)?(?:详情|详细内容|具体要求)?",
            clean,
        )
        or re.fullmatch(rf"{token}\s*(?:是)?(?:做什么的|什么内容|什么任务|详情)", clean)
    )
    if detail_command:
        return Intent(kind="detail", task_no=task_no, rule_id="DETAIL_BY_NO")
    if update_command:
        update_payload = re.split(
            r"(?:修改为|改成|改到|延期到?|推迟到?|转给|改给|修改|更新)",
            clean,
            maxsplit=1,
        )[-1]
        if _looks_question(update_payload) or _looks_negative_instruction(update_payload):
            return Intent(
                kind="clarify",
                task_no=task_no,
                message=f"没有修改 {task_no}。请明确说明新的负责人、标题或截止时间。",
                rule_id="UPDATE_NON_MUTATING",
            )
        parsed = parse_chinese_datetime(clean, now)
        if parsed.error:
            return Intent(kind="clarify", message=parsed.error, rule_id="UPDATE_TIME_AMBIGUOUS")
        new_assignee = None
        alias_re = _alias_pattern(aliases)
        person_match = re.search(rf"(?:转给|改给)\s*({alias_re})", clean) if alias_re else None
        if person_match:
            new_assignee = aliases[person_match.group(1)]
        title = None
        change = re.search(r"(?:改成|修改为)\s*(.+)$", clean)
        if change:
            candidate = _remove_time_phrases(change.group(1), parsed)
            candidate = re.sub(rf"(?:转给|改给)\s*(?:{alias_re})", "", candidate).strip(" ,，。")
            title = candidate or None
        if not parsed.value and not title and not new_assignee:
            return Intent(
                kind="clarify",
                message=f"请说明要怎么修改 {task_no}。",
                rule_id="UPDATE_MISSING_FIELDS",
            )
        return Intent(
            kind="update",
            task_no=task_no,
            title=title,
            due_at=parsed.value,
            new_assignee=new_assignee,
            rule_id="UPDATE_BY_NO",
        )
    return None


def _list_intent(text: str, aliases: dict[str, str]) -> Intent | None:
    clean = normalize_text(text).strip("。.!！?？ ")
    state = "active"
    if "回收站" in clean or "已删除" in clean:
        state = "deleted"
    elif "已完成" in clean or "完成的任务" in clean:
        state = "done"
    elif "全部任务" in clean or "所有任务" in clean:
        state = "all"

    mine_direct = re.fullmatch(
        r"(?:请)?(?:查看|查询|列出|展示|显示|看看)?(?:一下)?"
        r"(?:我的|我)(?:的)?(?:待办|任务|活)(?:清单|列表)?(?:情况)?",
        clean,
    )
    if mine_direct:
        return Intent(kind="list_self", state=state, rule_id="LIST_SELF")

    if re.fullmatch(
        r"(?:请|麻烦)?(?:给我|帮我)?\s*(?:看看|查看|查询|列出|展示|显示|发一下|发我)"
        r"(?:一下)?\s*(?:我的)?(?:任务|待办)(?:清单|列表)?",
        clean,
    ):
        return Intent(kind="list_self", state=state, rule_id="LIST_SELF_REQUEST")

    general = re.fullmatch(
        r"(?:请)?(?:查看|查询|列出|展示|显示|看看)?(?:一下)?"
        r"(?:全部|所有|我的|我)?(?:的)?(?:待办|任务|活)"
        r"(?:清单|列表)?(?:情况)?",
        clean,
    )
    if general or clean in {"回收站", "已完成任务", "全部待办", "任务清单", "展示任务"}:
        return Intent(kind="list", state=state, rule_id="LIST_GENERAL")

    if aliases:
        alias_re = _alias_pattern(aliases)
        person_list = re.fullmatch(
            rf"(?:请|麻烦)?(?:(?:给我|帮我)\s*)?(?:看看|查看|查询|列出|展示|显示|发一下)?"
            rf"(?:需要)?\s*({alias_re})\s*的?(?:任务|待办)(?:清单|列表)?",
            clean,
        )
        if person_list:
            return Intent(
                kind="list",
                assignee=aliases[person_list.group(1)],
                state=state,
                rule_id="LIST_BY_ASSIGNEE_REQUEST",
            )
        person = re.fullmatch(
            rf"({alias_re})\s*(?:还)?(?:有|剩下)(?:什么|哪些|多少)?(?:活|任务|待办)(?:要做)?",
            clean,
        )
        if person:
            return Intent(
                kind="list",
                assignee=aliases[person.group(1)],
                state=state,
                rule_id="LIST_BY_ASSIGNEE",
            )
        completed = re.fullmatch(rf"({alias_re})\s*(?:已完成|完成的)(?:任务|待办)", clean)
        if completed:
            return Intent(
                kind="list",
                assignee=aliases[completed.group(1)],
                state="done",
                rule_id="LIST_DONE_BY_ASSIGNEE",
            )

    mine = re.fullmatch(
        r"(?:我|自己)\s*(?:还)?(?:有|剩下)(?:什么|哪些|多少)?(?:活|任务|待办)(?:要做)?",
        clean,
    )
    if mine:
        return Intent(kind="list_self", state=state, rule_id="LIST_SELF")
    return None


def _clean_assignment_body(body: str) -> str:
    body = normalize_text(body)
    body = re.sub(
        r"^(?:安排|交代|指派)(?:一下|一个)?(?:任务|工作)?\s*[,，:：、]?\s*",
        "",
        body,
    )
    body = re.sub(r"^(?:任务|工作)\s*[,，:：、]?\s*", "", body)
    body = re.sub(r"^(?:去|来|要|需要|负责|请)\s*", "", body)
    return body.strip(" ,，:：、。.!！")


def _non_mutating_assignment(raw_text: str, body: str) -> bool:
    """对所有创建入口统一做“只读/否定/疑问”拦截。

    这里宁可要求用户把含糊句子说得更明确，也不能错误创建任务。
    """
    raw = normalize_text(raw_text).strip()
    clean_body = _clean_assignment_body(body)
    if not clean_body:
        return False
    if _looks_meta(raw):
        return True
    if _looks_negative_instruction(clean_body):
        return True
    if re.match(r"^(?:看看|查看|查询|显示|列出|介绍|告诉我|查一下|看一下)", clean_body):
        return True
    if _looks_question(clean_body):
        return True
    return False


def _build_create(
    assignee: str,
    body: str,
    raw_text: str,
    now: datetime,
    rule_id: str,
) -> Intent:
    if _non_mutating_assignment(raw_text, body):
        return Intent(
            kind="clarify",
            assignee=assignee,
            message="这句话像查询、否定或取消指令，本次没有创建任务。请明确说“给某人安排任务：具体事项”。",
            rule_id="CREATE_NON_MUTATING",
        )
    body = _clean_assignment_body(body)
    if not body:
        return Intent(
            kind="clarify",
            assignee=assignee,
            message=f"要给{assignee}安排什么任务？",
            rule_id="CREATE_MISSING_BODY",
        )
    parsed = parse_chinese_datetime(body, now)
    if parsed.error:
        return Intent(kind="clarify", assignee=assignee, message=parsed.error, rule_id="CREATE_TIME_AMBIGUOUS")
    title = _remove_time_phrases(body, parsed) if parsed.value else body
    title = re.sub(r"^的\s*", "", title).strip(" ,，:：、。.!！")
    if not title:
        title = body
    return Intent(
        kind="create",
        assignee=assignee,
        title=title[:240],
        content=body,
        due_at=parsed.value,
        rule_id=rule_id,
    )


def _assignment_intent(
    text: str,
    aliases: dict[str, str],
    self_name: str | None,
    now: datetime,
) -> Intent | None:
    clean = normalize_text(text).strip()
    alias_re = _alias_pattern(aliases)
    person_re = rf"(?:{alias_re}|我)" if alias_re else r"我"
    if _multiple_assignees(clean, aliases, self_name):
        return Intent(
            kind="clarify",
            message="当前任务系统一次只能指定一个负责人，请拆成两条任务。",
            rule_id="CREATE_MULTIPLE_ASSIGNEES",
        )

    patterns = (
        (
            rf"^(?:请|麻烦|帮我|帮忙)?\s*给\s*(?P<person>{person_re})\s*"
            rf"(?P<body>(?:安排|交代|指派|新增|创建)(?:一下|一个)?(?:任务|待办|工作)?.*)$",
            "ASSIGN_EXPLICIT",
        ),
        (
            rf"^(?:请|麻烦|帮我|帮忙)?\s*(?P<verb>让|叫|通知)\s*"
            rf"(?P<person>{person_re})\s*(?P<body>.+)$",
            "ASSIGN_DIRECTIVE",
        ),
        (
            rf"^(?:请|麻烦)?\s*(?:需要)\s*(?P<person>{person_re})\s*(?P<body>.+)$",
            "ASSIGN_NEED",
        ),
        (
            rf"^(?P<person>{person_re})\s*(?:需要|要|负责|请)\s*(?P<body>.+)$",
            "ASSIGN_PERSON_FIRST",
        ),
        (
            rf"^(?:由)\s*(?P<person>{person_re})\s*负责\s*(?P<body>.+)$",
            "ASSIGN_RESPONSIBLE",
        ),
        (
            rf"^(?:把)\s*(?P<body>.+?)\s*交给\s*(?P<person>{person_re})(?:负责|处理|完成)?$",
            "ASSIGN_GIVE",
        ),
    )
    for pattern, rule_id in patterns:
        match = re.match(pattern, clean)
        if not match:
            continue
        person = match.group("person")
        if person == "我" and rule_id != "ASSIGN_EXPLICIT":
            return None
        assignee = self_name if person == "我" else aliases.get(person)
        if not assignee:
            return Intent(
                kind="clarify",
                message="当前渠道还没有绑定负责人，不能把“我”当作任务负责人。",
                rule_id="SELF_NOT_BOUND",
            )
        body = match.group("body")
        body_clean = _clean_assignment_body(body)
        if re.search(r"什么任务|哪些任务|任务清单|任务列表|看看任务|查看任务|查询任务", body_clean):
            return None
        if rule_id == "ASSIGN_DIRECTIVE":
            prefix = match.group("verb")
            raw_body = normalize_text(body)
            if prefix != "通知" and re.match(r"^(?:发|发送)(?:一条)?消息", raw_body):
                return None
        return _build_create(assignee, body, clean, now, rule_id)

    if alias_re:
        direct = re.match(rf"^(?P<person>{alias_re})\s*(?P<body>.+)$", clean)
        if direct:
            body = direct.group("body")
            has_date = bool(
                re.search(
                    r"今天|明天|后天|周[一二三四五六日天]|星期[一二三四五六日天]|"
                    r"\d{1,2}月\d{1,2}|上午|下午|晚上|\d{1,2}:\d{2}|\d{1,2}点",
                    body,
                )
            )
            has_action = bool(
                re.search(r"发|发布|开会|参加|完成|准备|联系|跟进|提交|制作|整理|处理|去", body)
            )
            if has_date and has_action and not clean.endswith(("吗", "呢", "?", "？")):
                return _build_create(
                    aliases[direct.group("person")],
                    body,
                    clean,
                    now,
                    "ASSIGN_DIRECT",
                )

    unknown = re.match(
        r"^(?:请|麻烦|帮我|帮忙)?\s*(?:给|让|通知)\s*"
        r"(?P<person>[\u4e00-\u9fffA-Za-z0-9_-]{1,12})\s*"
        r"(?:安排|交代|指派)(?:一下|一个)?(?:任务|工作)?",
        clean,
    )
    if unknown and unknown.group("person") not in aliases:
        names = "、".join(sorted(set(aliases.values()))) or "暂无人员"
        return Intent(
            kind="clarify",
            message=f"没有找到负责人“{unknown.group('person')}”。当前可选：{names}。",
            rule_id="UNKNOWN_ASSIGNEE",
        )
    return None


def _split_numbered_items(text: str) -> tuple[str, list[str]] | None:
    matches = list(NUMBERED_ITEM_RE.finditer(text))
    if len(matches) < 2:
        return None
    prefix = text[: matches[0].start()].strip()
    items = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        item = text[match.end():end].strip()
        if item:
            items.append(item)
    return (prefix, items) if len(items) >= 2 else None


def _has_item_assignee_surface(text: str, aliases: dict[str, str]) -> bool:
    clean = normalize_text(text)
    polite = r"(?:请|麻烦|帮我|帮忙)?\s*"
    known_people = rf"(?:{_alias_pattern(aliases)}|我)" if aliases else r"我"
    any_person = r"[\u4e00-\u9fffA-Za-z0-9_-]{1,12}"
    return bool(
        re.match(rf"^{polite}(?:给|让|叫|通知|需要)\s*(?:{known_people}|{any_person})", clean)
        or re.match(rf"^{polite}(?:{known_people}|{any_person})\s*(?:需要|要|负责|请)", clean)
        or re.match(rf"^{polite}由\s*(?:{known_people}|{any_person})\s*负责", clean)
        or re.search(rf"交给\s*(?:{known_people}|{any_person})(?:负责|处理|完成)?$", clean)
    )


def parse_message(
    text: str,
    staff: Iterable[StaffIdentity],
    *,
    self_name: str | None = None,
    now: datetime | None = None,
    configured_aliases: dict[str, str] | None = None,
) -> list[Intent] | None:
    clean = normalize_text(text)
    if not clean or clean.startswith(("/", "!")):
        return None
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    aliases = _alias_map(staff, configured_aliases)

    if _looks_meta(clean):
        return None

    if _calendar_requested(clean):
        alias_re = _alias_pattern(aliases)
        assignment_surface = bool(
            alias_re
            and (
                re.search(rf"(?:给|让|叫|通知|需要)\s*(?:{alias_re}|我)", clean)
                or re.search(rf"交给\s*(?:{alias_re}|我)", clean)
            )
        )
        if assignment_surface:
            return [
                Intent(
                    kind="clarify",
                    message="这句话同时包含任务和日历操作。请先安排任务，再单独说“同步到日历”。",
                    rule_id="TASK_CALENDAR_CONFLICT",
                )
            ]
        return None

    numbered = _split_numbered_items(clean)
    if numbered:
        prefix, items = numbered
        if prefix and (
            _looks_meta(prefix)
            or _looks_question(prefix)
            or _looks_negative_instruction(prefix)
        ):
            return None
        inherited_assignee = None
        if prefix:
            header = _assignment_intent(prefix, aliases, self_name, now)
            if header and header.kind == "clarify" and not header.assignee:
                return [header]
            if header and header.assignee:
                inherited_assignee = header.assignee
            elif not re.fullmatch(
                r"(?:请|麻烦)?\s*(?:安排|新增|创建|记录)?\s*"
                r"(?:以下|这些)?\s*(?:任务|待办)(?:如下)?\s*[:：]?",
                prefix,
            ):
                return None
        parsed_items: list[Intent] = []
        for item in items:
            parsed = _assignment_intent(item, aliases, self_name, now)
            if parsed is None and inherited_assignee:
                if _has_item_assignee_surface(item, aliases):
                    parsed = Intent(
                        kind="clarify",
                        message="编号任务中的负责人表述不明确，本批任务一条也没有创建。请每项使用“让某人做……”或去掉项内姓名。",
                        rule_id="NUMBERED_ASSIGNEE_AMBIGUOUS",
                    )
                else:
                    parsed = _build_create(
                        inherited_assignee, item, item, now, "ASSIGN_NUMBERED_INHERITED"
                    )
            if parsed is None or parsed.kind not in {"create", "clarify"}:
                return None
            parsed_items.append(parsed)
        if parsed_items:
            return parsed_items

    numbered_intent = _task_number_intent(clean, aliases, self_name, now)
    if numbered_intent:
        if _calendar_requested(clean):
            return None
        return [numbered_intent]

    list_intent = _list_intent(clean, aliases)
    if list_intent:
        return [list_intent]

    if clean.rstrip().endswith(("吗", "么", "呢", "?", "？")):
        return None

    if is_complete_all_command(clean):
        if not self_name:
            return [Intent(kind="clarify", message="当前渠道还没有绑定负责人。", rule_id="SELF_NOT_BOUND")]
        return [Intent(kind="complete_all", assignee=self_name, rule_id="COMPLETE_ALL")]

    bare_complete = re.fullmatch(
        rf"(?:(?P<person>{_alias_pattern(aliases)}|我)\s*)?(?:{'|'.join(COMPLETE_WORDS)})了?",
        clean.strip("。.!！ "),
    ) if aliases else re.fullmatch(r"我?\s*(?:完成|做完|干完|搞定|办完)了?", clean)
    if bare_complete:
        person = bare_complete.groupdict().get("person") if hasattr(bare_complete, "groupdict") else None
        assignee = self_name if not person or person == "我" else aliases.get(person)
        if not assignee:
            return [Intent(kind="clarify", message="当前渠道还没有绑定负责人。", rule_id="SELF_NOT_BOUND")]
        if assignee != self_name:
            return [Intent(kind="clarify", message="只能完成当前消息账号自己的任务。", rule_id="COMPLETE_OTHER_FORBIDDEN")]
        return [Intent(kind="complete_unique", assignee=assignee, rule_id="COMPLETE_UNIQUE")]

    assignment = _assignment_intent(clean, aliases, self_name, now)
    if assignment:
        return [assignment]
    return None


def looks_like_task_message(text: str) -> bool:
    clean = normalize_text(text)
    if not clean or clean.startswith(("/", "!")):
        return False
    return bool(
        TASK_NO_RE.search(clean)
        or is_complete_all_command(clean)
        or re.search(r"任务|待办|回收站|还有什么活|有哪些活", clean)
        or re.search(r"给|让|通知|交给|负责|需要|安排|干完|做完|搞定", clean)
    )


def looks_like_explicit_task_message(text: str) -> bool:
    clean = normalize_text(text)
    if not clean:
        return False
    named_people = (
        r"杰哥(?!们|哥|姐|弟|妹)|靓仔(?!们)|"
        r"敏总(?!监|经理|裁|管|助理|秘书|们)|姐(?!姐|夫|妹|弟|们)|"
        r"振华(?!们)"
    )
    people = rf"(?:{named_people}|我)"
    assignment_surface = bool(
        re.search(
            rf"给\s*(?:{people})\s*(?:安排|交代|指派|新增|创建)(?:一下|一个)?(?:任务|待办|工作)?",
            clean,
        )
        or re.search(rf"(?:让|叫|通知|需要)\s*(?:{named_people})", clean)
        or re.search(rf"(?:{named_people})\s*(?:需要|要|负责|还有什么活)", clean)
        or re.search(r"(?:给|让|通知)\s*[\u4e00-\u9fffA-Za-z0-9_-]{1,12}\s*(?:安排|交代|指派)", clean)
    )
    if _looks_meta(clean):
        return bool(
            TASK_NO_RE.search(clean)
            or assignment_surface
            or re.search(r"任务|待办|提醒|误识别|识别错", clean)
        )
    if _calendar_requested(clean):
        return assignment_surface
    if re.fullmatch(r"(?i)\s*T\s*0*[1-9]\d*\s*", clean):
        return True
    if TASK_NO_RE.search(clean) and _contains_any(
        clean,
        (*COMPLETE_WORDS, *DELETE_WORDS, *RESTORE_WORDS, *REOPEN_WORDS, *DETAIL_WORDS, *UPDATE_WORDS),
    ):
        return True
    if re.search(r"查看.*(?:任务|待办)|任务清单|全部待办|还有什么活|有哪些任务|回收站", clean):
        return True
    return bool(
        assignment_surface
        or is_complete_all_command(clean)
        or re.fullmatch(rf"(?:(?:{people})\s*)?(?:完成|做完|干完|搞定|办完)了?", clean)
    )


def _read_env_file(name: str) -> str:
    candidates = []
    if os.environ.get("HERMES_HOME"):
        candidates.append(Path(os.environ["HERMES_HOME"]) / ".env")
    candidates.extend((Path("/opt/data/.env"), Path.home() / ".hermes" / ".env"))
    for path in candidates:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""


def env_value(name: str, default: str = "") -> str:
    return os.environ.get(name) or _read_env_file(name) or default


class TaskApi:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or env_value("TASK_API_BASE_URL")).rstrip("/")
        self.api_key = api_key or env_value("TASK_API_KEY")
        if not self.base_url or not self.api_key:
            raise RuntimeError("任务后台未配置")

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json", "X-SignalDesk-Key": self.api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                if response.status == 204:
                    return None
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw).get("detail", raw)
            except json.JSONDecodeError:
                detail = raw or f"HTTP {exc.code}"
            raise RuntimeError(str(detail)) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("任务后台暂时无法连接") from exc

    def list_staff(self) -> list[StaffIdentity]:
        result = self.request("GET", "/api/v1/staff?include_disabled=true")
        return [
            StaffIdentity(
                id=int(item["id"]),
                name=str(item["name"]).strip(),
                channels=tuple(item.get("channels") or ()),
                enabled=bool(item.get("enabled", True)),
            )
            for item in result
        ]

    def list_tasks(self, state: str = "active", assignee_id: int | None = None) -> dict[str, Any]:
        query: dict[str, str] = {"state": state, "limit": "500"}
        if assignee_id is not None:
            query["assignee_id"] = str(assignee_id)
        return self.request("GET", f"/api/v1/tasks?{urllib.parse.urlencode(query)}")

    def get_task(self, task_no: str, include_deleted: bool = True) -> dict[str, Any]:
        query = urllib.parse.urlencode({"include_deleted": str(include_deleted).lower()})
        try:
            return self.request("GET", f"/api/v1/tasks/by-number/{task_no}?{query}")
        except RuntimeError:
            state = "all" if include_deleted else "active"
            result = self.list_tasks(state)
            exact = [item for item in result["items"] if item["task_no"].upper() == task_no.upper()]
            if len(exact) != 1:
                raise RuntimeError(f"没有找到任务 {task_no}")
            return exact[0]

    def create_task(
        self,
        draft: Intent,
        staff: StaffIdentity,
        creator: str,
        platform: str,
        source_message_id: str | None,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/v1/tasks",
            {
                "title": draft.title,
                "content": draft.content,
                "assignee_id": staff.id,
                "creator_name": creator,
                "priority": 2,
                "due_at": draft.due_at.replace(tzinfo=None).isoformat(timespec="minutes")
                if draft.due_at
                else None,
                "remind_enabled": True,
                "remind_interval_minutes": 300,
                "source_platform": platform,
                "source_message_id": source_message_id,
            },
        )

    def update_task(
        self,
        task: dict[str, Any],
        intent: Intent,
        operator: str,
        staff_by_name: dict[str, StaffIdentity],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"operator_name": operator}
        if intent.title:
            payload["title"] = intent.title
        if intent.due_at:
            payload["due_at"] = intent.due_at.replace(tzinfo=None).isoformat(timespec="minutes")
        if intent.new_assignee:
            payload["assignee_id"] = staff_by_name[intent.new_assignee].id
        return self.request("PATCH", f"/api/v1/tasks/{task['id']}", payload)

    def action(self, action: str, task: dict[str, Any], operator: str) -> dict[str, Any]:
        if action == "delete":
            query = urllib.parse.urlencode({"operator_name": operator})
            return self.request("DELETE", f"/api/v1/tasks/{task['id']}?{query}")
        return self.request(
            "POST",
            f"/api/v1/tasks/{task['id']}/{action}",
            {"operator_name": operator},
        )

    def complete_from_channel(
        self,
        platform: str,
        recipient_id: str,
        *,
        task_no: str | None = None,
        all_active: bool = False,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/v1/channel-task-completions",
            {
                "platform": platform,
                "recipient_id": recipient_id,
                "task_no": task_no,
                "all_active": all_active,
            },
        )


def _parse_env_json(name: str) -> dict[str, str]:
    raw = env_value(name)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {str(key): str(item) for key, item in value.items()} if isinstance(value, dict) else {}


def _due_label(value: str | datetime | None) -> str:
    if not value:
        return "未设置"
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(SHANGHAI)
    return parsed.strftime("%m-%d %H:%M")


def _status_label(value: str, deleted_at: Any = None) -> str:
    if deleted_at:
        return "🗑️ 已删除"
    return {"todo": "⏳ 待办", "doing": "🔄 进行中", "done": "✅ 已完成"}.get(value, value)


def _escape_table(value: Any) -> str:
    return str(value or "").replace("|", "｜").replace("\r", " ").replace("\n", " ").strip()


def format_task_list(tasks: list[dict[str, Any]], lead: str = "") -> str:
    todo = sum(1 for item in tasks if item.get("status") == "todo" and not item.get("deleted_at"))
    doing = sum(1 for item in tasks if item.get("status") == "doing" and not item.get("deleted_at"))
    done = sum(1 for item in tasks if item.get("status") == "done" and not item.get("deleted_at"))
    lines = []
    if lead:
        lines.extend((lead, ""))
    lines.extend(
        (
            "## 📋 任务清单",
            f"> 共 {len(tasks)} 项｜待办 {todo}｜进行中 {doing}｜已完成 {done}",
            "",
        )
    )
    if not tasks:
        lines.append("暂无符合条件的任务。")
        return "\n".join(lines)
    lines.extend(
        (
            "| 编号 | 负责人 | 任务 | 截止时间 | 状态 |",
            "|---|---|---|---|---|",
        )
    )
    for item in tasks:
        lines.append(
            "| {task_no} | {assignee} | {title} | {due} | {status} |".format(
                task_no=_escape_table(item.get("task_no")),
                assignee=_escape_table(item.get("assignee_name")),
                title=_escape_table(item.get("title")),
                due=_due_label(item.get("due_at")),
                status=_status_label(str(item.get("status")), item.get("deleted_at")),
            )
        )
    lines.extend(("", "操作：`完成 T编号`｜`详情 T编号`｜`删除 T编号`"))
    return "\n".join(lines)


def format_detail(task: dict[str, Any]) -> str:
    content = str(task.get("content") or "").strip() or "无补充要求"
    return "\n".join(
        (
            f"## {task['task_no']} {_escape_table(task['title'])}",
            f"- 负责人：{task['assignee_name']}",
            f"- 状态：{_status_label(task['status'], task.get('deleted_at'))}",
            f"- 截止：{_due_label(task.get('due_at'))}",
            f"- 安排人：{task.get('creator_name') or '未记录'}",
            f"- 具体要求：{content}",
            "- 提醒：未完成时每 5 小时提醒一次",
        )
    )


class TaskRouter:
    def __init__(self, api: TaskApi | None = None):
        self.api = api or TaskApi()
        self.aliases = _parse_env_json("TASK_ROUTER_ALIASES")
        self.creator_by_identity = {
            **DEFAULT_CREATOR_BY_IDENTITY,
            **_parse_env_json("TASK_ROUTER_CREATOR_BY_IDENTITY"),
        }

    @staticmethod
    def _staff_by_name(staff: list[StaffIdentity]) -> dict[str, StaffIdentity]:
        return {item.name: item for item in staff if item.enabled}

    def _sender_staff(
        self,
        staff: list[StaffIdentity],
        platform: str,
        sender_id: str | None,
        chat_id: str | None,
        chat_type: str,
    ) -> str | None:
        candidate = str(sender_id) if sender_id else None
        if candidate is None and chat_type == "dm" and chat_id:
            candidate = str(chat_id)
        if candidate is None:
            return None
        matched = []
        for person in staff:
            if not person.enabled:
                continue
            for channel in person.channels:
                if (
                    channel.get("enabled", True)
                    and normalize_platform(str(channel.get("platform"))) == platform
                    and str(channel.get("recipient_id")) == candidate
                ):
                    matched.append(person.name)
                    break
        unique = list(dict.fromkeys(matched))
        return unique[0] if len(unique) == 1 else None

    def handle(
        self,
        text: str,
        *,
        platform: str,
        sender_id: str | None,
        sender_name: str | None = None,
        chat_id: str | None,
        chat_type: str = "dm",
        message_id: str | None,
        now: datetime | None = None,
    ) -> str | None:
        prepared = self.prepare(
            text,
            platform=platform,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_id=chat_id,
            chat_type=chat_type,
            message_id=message_id,
            now=now,
        )
        return self.execute(prepared) if prepared else None

    def prepare(
        self,
        text: str,
        *,
        platform: str,
        sender_id: str | None,
        sender_name: str | None = None,
        chat_id: str | None,
        chat_type: str = "dm",
        message_id: str | None,
        now: datetime | None = None,
    ) -> PreparedRoute | None:
        if not looks_like_task_message(text):
            return None
        platform = normalize_platform(platform)
        staff = self.api.list_staff()
        staff_by_name = self._staff_by_name(staff)
        self_name = self._sender_staff(staff, platform, sender_id, chat_id, chat_type)
        recipient_id = str(sender_id) if sender_id else (
            str(chat_id) if chat_type == "dm" and chat_id else None
        )
        intents = parse_message(
            text,
            staff,
            self_name=self_name,
            now=now,
            configured_aliases=self.aliases,
        )
        if not intents:
            return None
        creator = (
            self.creator_by_identity.get(f"{platform}:{self_name}")
            if self_name
            else None
        ) or (sender_name or "").strip() or self_name or "Hermes 用户"
        return PreparedRoute(
            intents=tuple(intents),
            staff=tuple(staff),
            platform=platform,
            self_name=self_name,
            recipient_id=recipient_id if self_name else None,
            creator=creator,
            message_id=message_id,
        )

    def execute(self, prepared: PreparedRoute) -> str:
        intents = list(prepared.intents)
        staff = list(prepared.staff)
        platform = prepared.platform
        self_name = prepared.self_name
        recipient_id = prepared.recipient_id
        creator = prepared.creator
        message_id = prepared.message_id
        staff_by_name = self._staff_by_name(staff)
        if not self_name or not recipient_id or self_name not in staff_by_name:
            return "当前发送账号没有绑定负责人，或人员已停用、移除。请联系管理员在用户管理中启用人员并绑定消息渠道。"
        if any(item.kind == "clarify" for item in intents):
            return next(item.message for item in intents if item.kind == "clarify") or "请再明确一点。"

        created = []
        for index, intent in enumerate(intents, start=1):
            if intent.kind != "create":
                continue
            assignee = staff_by_name.get(intent.assignee or "")
            if not assignee:
                return f"没有找到负责人“{intent.assignee}”。"
            source_id = None
            if message_id:
                source_id = f"{message_id}:{index}" if len(intents) > 1 else str(message_id)
            created.append(
                self.api.create_task(intent, assignee, creator, platform, source_id)
            )
        if created:
            if len(created) == 1:
                item = created[0]
                return "\n".join(
                    (
                        f"✅ 任务已创建：{item['task_no']}",
                        f"负责人：{item['assignee_name']}",
                        f"任务：{item['title']}",
                        f"截止：{_due_label(item.get('due_at'))}",
                        f"已安排立即通知{item['assignee_name']}；未完成时每 5 小时提醒。",
                    )
                )
            lines = [f"✅ 已创建 {len(created)} 项任务", "", "| 编号 | 负责人 | 任务 | 截止 |", "|---|---|---|---|"]
            for item in created:
                lines.append(
                    f"| {item['task_no']} | {item['assignee_name']} | "
                    f"{_escape_table(item['title'])} | {_due_label(item.get('due_at'))} |"
                )
            lines.extend(("", "已安排立即通知各负责人；未完成时每 5 小时提醒。"))
            return "\n".join(lines)

        intent = intents[0]
        if intent.kind in {"list", "list_self"}:
            assignee_name = intent.assignee
            if intent.kind == "list_self":
                assignee_name = self_name
                if not assignee_name or assignee_name not in staff_by_name:
                    return "当前发送账号没有绑定负责人，无法判断“我的任务”指谁。"
            assignee_id = staff_by_name[assignee_name].id if assignee_name in staff_by_name else None
            result = self.api.list_tasks(intent.state, assignee_id)
            return format_task_list(result["items"])

        if intent.kind == "complete_unique":
            assignee = staff_by_name.get(intent.assignee or "")
            if not assignee or not recipient_id:
                return "当前渠道没有绑定负责人。"
            result = self.api.list_tasks("active", assignee.id)
            tasks = result["items"]
            if len(tasks) != 1:
                return format_task_list(
                    tasks,
                    lead="请回复具体任务编号，例如“完成 T2”。" if tasks else "你当前没有未完成任务。",
                )
            result = self.api.complete_from_channel(
                platform,
                recipient_id,
                task_no=tasks[0]["task_no"],
            )
            task = result["items"][0]
            return f"✅ {task['task_no']} 已完成：{task['title']}\n后续提醒已停止。"

        if intent.kind == "complete_all":
            if not self_name or not recipient_id:
                return "当前发送账号没有绑定负责人，无法完成任务。"
            result = self.api.complete_from_channel(
                platform,
                recipient_id,
                all_active=True,
            )
            count = int(result.get("completed_count") or 0)
            if count == 0:
                return "你当前没有未完成任务。"
            return f"✅ 已完成你的全部 {count} 项任务，后续提醒已停止。"

        task = self.api.get_task(intent.task_no or "", include_deleted=True)
        if intent.kind == "detail":
            return format_detail(task)
        if intent.kind == "update":
            task = self.api.update_task(task, intent, creator, staff_by_name)
            return (
                f"✅ {task['task_no']} 已修改\n负责人：{task['assignee_name']}\n"
                f"任务：{task['title']}\n截止：{_due_label(task.get('due_at'))}"
            )
        if intent.kind in {"complete", "delete", "restore", "reopen"}:
            if intent.kind == "complete":
                if not self_name or not recipient_id:
                    return "当前发送账号没有绑定负责人，无法完成任务。"
                if task.get("assignee_name") != self_name:
                    return "只能完成当前消息账号自己的任务。"
                result = self.api.complete_from_channel(
                    platform,
                    recipient_id,
                    task_no=task["task_no"],
                )
                task = result["items"][0]
            else:
                task = self.api.action(intent.kind, task, creator)
            labels = {
                "complete": "已完成，后续提醒已停止",
                "delete": "已删除，可在回收站恢复",
                "restore": "已恢复",
                "reopen": "已重新打开",
            }
            return f"✅ {task['task_no']} {labels[intent.kind]}：{task['title']}"
        return None
