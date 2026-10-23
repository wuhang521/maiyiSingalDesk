"""Public list-price estimates; reservations are application policy, not provider caps."""

from decimal import Decimal, InvalidOperation, ROUND_UP
from typing import Any


SCENE_MODEL = "doubao-seedance-2-0-mini-260615"
SCENE_SOURCE_URL = "https://docs.volcengine.com/docs/ark/model-pricing?lang=zh"
SCENE_VERIFIED_AT = "2026-10-22"
SCENE_UNIT_PRICE = Decimal("23")  # CNY per million tokens, no input video.
_SCENE_PIXELS = {
    "480p": {"16:9": (864, 496), "4:3": (752, 560), "1:1": (640, 640),
             "3:4": (560, 752), "9:16": (496, 864), "21:9": (992, 432)},
    "720p": {"16:9": (1280, 720), "4:3": (1112, 834), "1:1": (960, 960),
             "3:4": (834, 1112), "9:16": (720, 1280), "21:9": (1470, 630)},
}


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_UP))


def official_scene_quote(seconds: int, resolution: str = "720p", ratio: str = "9:16") -> dict[str, Any]:
    """Estimate a fixed-duration text/image-to-video Mini request at public list price.

    An additional second per scene and 20% reserve account for timing/usage variation.
    The provider explicitly defines its token formula as an estimate, so neither amount
    is a guaranteed charge ceiling. Usage returned by a completed task is authoritative.
    Video reference inputs, adaptive ratio, flex inference and other models are excluded.
    """
    try:
        if isinstance(seconds, bool):
            raise ValueError()
        duration = Decimal(str(seconds))
        if not duration.is_finite() or duration != duration.to_integral_value() or not 4 <= duration <= 15:
            raise ValueError()
        width, height = _SCENE_PIXELS[resolution][ratio]
    except (InvalidOperation, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Official Mini estimate requires 4–15 integer seconds and a supported fixed output size") from exc
    tokens_per_second = Decimal(width * height * 24) / 1024
    tokens = (duration * tokens_per_second).to_integral_value(rounding=ROUND_UP)
    reservation_tokens = ((duration + 1) * tokens_per_second).to_integral_value(rounding=ROUND_UP)
    return {
        "amount": _money(tokens * SCENE_UNIT_PRICE / 1_000_000),
        "reservation_amount": _money(reservation_tokens * SCENE_UNIT_PRICE * Decimal("1.2") / 1_000_000),
        "currency": "CNY",
        "pricing_kind": "estimate",
        "unit_price": float(SCENE_UNIT_PRICE),
        "source_url": SCENE_SOURCE_URL,
        "verified_at": SCENE_VERIFIED_AT,
        "model": SCENE_MODEL,
        "estimated_tokens": int(tokens),
        "resolution": resolution,
        "ratio": ratio,
        "duration_seconds": int(duration),
        "reservation_extra_seconds": 1,
        "reservation_multiplier": 1.2,
        "basis": "官方无视频输入刊例价估算；每镜另预留 1 秒及 20% 余量，非供应商保证上限，按实际 token 对账",
    }


def actual_scene_cost(usage: Any) -> float | None:
    """Price safe authoritative usage from a successful, matching Mini request.

    The caller must verify successful status, model, and no video input before applying
    this price. Missing or malformed usage remains unknown instead of becoming free.
    """
    if not isinstance(usage, dict):
        return None
    tokens = usage.get("completion_tokens")
    if type(tokens) is not int or not 0 <= tokens <= 10**12:
        return None
    return _money(Decimal(tokens) * SCENE_UNIT_PRICE / 1_000_000)
