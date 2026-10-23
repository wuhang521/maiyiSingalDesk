from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def within_reminder_window(
    now_local: datetime,
    start_hour: int,
    end_hour: int,
) -> bool:
    return start_hour <= now_local.astimezone(SHANGHAI).hour < end_hour


def next_reminder_at(
    now_utc: datetime,
    interval_minutes: int,
    start_hour: int,
    end_hour: int,
) -> datetime:
    """返回 UTC 无时区时间；落在静默时段时顺延到下一个发送窗口。"""
    aware_utc = now_utc.replace(tzinfo=timezone.utc) if now_utc.tzinfo is None else now_utc
    candidate = (aware_utc + timedelta(minutes=interval_minutes)).astimezone(SHANGHAI)
    if candidate.hour < start_hour:
        candidate = candidate.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    elif candidate.hour >= end_hour:
        candidate = (candidate + timedelta(days=1)).replace(
            hour=start_hour,
            minute=0,
            second=0,
            microsecond=0,
        )
    return candidate.astimezone(timezone.utc).replace(tzinfo=None)
