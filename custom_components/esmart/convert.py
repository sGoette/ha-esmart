"""Value conversions between eSmart and Home Assistant (pure functions)."""

from __future__ import annotations

from typing import Any

RAW_MAX = 1024  # eSmart blinds: position/orientation 0..1024

# Node type ids seen in the structure message
TYPE_LIGHT = 2
TYPE_DIMMER = 3
TYPE_BLIND = 4
TYPE_HEATING = 6


def parse_dimming(value: str | int | float | None) -> int:
    """'42%' -> 42."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, min(100, int(value)))
    try:
        return max(0, min(100, int(float(str(value).strip().rstrip("%")))))
    except ValueError:
        return 0


def pct_to_brightness(pct: int) -> int:
    return round(pct * 255 / 100)


def brightness_to_pct(brightness: int) -> int:
    return max(1, min(100, round(brightness * 100 / 255)))


def raw_to_cover_position(raw: int | float | None) -> int | None:
    """eSmart 0 = fully up/open, 1024 = fully down. HA 100 = open, 0 = closed."""
    if raw is None:
        return None
    return round(100 - max(0, min(RAW_MAX, raw)) * 100 / RAW_MAX)


def cover_position_to_raw(position: int) -> int:
    return round((100 - max(0, min(100, position))) * RAW_MAX / 100)


def raw_to_tilt(raw: int | float | None) -> int | None:
    """eSmart orientation 0 (slats open) .. 1024 (closed) -> HA tilt 100 (open) .. 0 (closed)."""
    if raw is None:
        return None
    return round(100 - max(0, min(RAW_MAX, raw)) * 100 / RAW_MAX)


def tilt_to_raw(tilt: int) -> int:
    return round((100 - max(0, min(100, tilt))) * RAW_MAX / 100)


def is_on(data: dict) -> bool | None:
    value = data.get("onoff", data.get("deviceOnOff"))
    if value is None:
        return None
    return str(value).lower() == "on"


POSITION_TOLERANCE = 16  # raw units of 1024; eSmart reports e.g. 286 after being sent 287


def field_matches(key: str, sent: Any, reported: Any) -> bool:
    """True if a value reported by eSmart confirms a value we sent."""
    if reported is None:
        return False
    if key == "dimming":
        return abs(parse_dimming(sent) - parse_dimming(reported)) <= 2
    if key == "onoff":
        return str(sent).lower() == str(reported).lower()
    try:
        tolerance = POSITION_TOLERANCE if key in ("position", "orientation") else 0.01
        return abs(float(sent) - float(reported)) <= tolerance
    except (TypeError, ValueError):
        return sent == reported
