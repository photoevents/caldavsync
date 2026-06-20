"""Configuration loading and validation for CalDAVSync."""

from __future__ import annotations

import os
from typing import Any

import yaml


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


def load_config(path: str = "config.yaml") -> dict[str, Any]:
    """Load and validate the YAML config, returning a plain dict.

    Supports either multiple calendar pairs (``calendar_pairs:``) or the legacy
    single-pair form (``google.calendar_id`` + ``nextcloud.calendar_name``).
    The result always exposes a normalized ``calendar_pairs`` list.

    Raises ConfigError with an actionable message on any problem.
    """
    if not os.path.exists(path):
        raise ConfigError(
            f"Config file not found: {path!r}. "
            "Copy config.example.yaml to config.yaml and fill it in."
        )

    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Could not parse {path!r}: {exc}") from exc

    # Connection-level requirements (shared across all pairs).
    _require(data, "google", ["credentials_file", "token_file"])
    _require(data, "nextcloud", ["url", "username", "password"])
    _require(data, "sync", ["direction"])

    sync = data["sync"]
    sync.setdefault("conflict_resolution", "newest_wins")

    data["calendar_pairs"] = _normalize_pairs(
        data, data["sync"]["direction"], sync["conflict_resolution"]
    )

    data.setdefault("logging", {})
    data["logging"].setdefault("level", "INFO")
    data["logging"].setdefault("file", "caldavsync.log")

    sync.setdefault("interval_seconds", 300)
    sync.setdefault("sync_past_days", 30)
    sync.setdefault("sync_future_days", 365)
    sync.setdefault("delete_propagation", True)
    sync.setdefault("send_invitations", False)
    sync.setdefault("state_db", "sync.db")

    return data


def _require(data: dict, section: str, keys: list[str]) -> None:
    if section not in data or not isinstance(data[section], dict):
        raise ConfigError(f"Missing config section: [{section}]")
    for key in keys:
        if data[section].get(key) in (None, ""):
            raise ConfigError(f"Missing config value: {section}.{key}")


_VALID_DIRECTIONS = ("google_to_caldav", "caldav_to_google", "bidirectional")
_VALID_CONFLICT = ("newest_wins", "google_wins", "caldav_wins")


def _normalize_pairs(
    data: dict, default_direction: str, default_conflict: str
) -> list[dict[str, str]]:
    pairs = data.get("calendar_pairs")
    if pairs:
        if not isinstance(pairs, list):
            raise ConfigError("calendar_pairs must be a list")
        normalized = []
        seen = set()
        for i, p in enumerate(pairs):
            for key in ("google_calendar_id", "caldav_calendar_name"):
                if not p.get(key):
                    raise ConfigError(f"calendar_pairs[{i}] missing {key}")
            name = p.get("name") or p["caldav_calendar_name"]
            if name in seen:
                raise ConfigError(f"Duplicate calendar pair name: {name!r}")
            seen.add(name)
            # Each pair may override the global sync.direction / conflict policy.
            direction = p.get("direction", default_direction)
            if direction not in _VALID_DIRECTIONS:
                raise ConfigError(
                    f"calendar_pairs[{i}] has invalid direction {direction!r}; "
                    f"expected one of {_VALID_DIRECTIONS}"
                )
            conflict = p.get("conflict_resolution", default_conflict)
            if conflict not in _VALID_CONFLICT:
                raise ConfigError(
                    f"calendar_pairs[{i}] has invalid conflict_resolution {conflict!r}; "
                    f"expected one of {_VALID_CONFLICT}"
                )
            normalized.append(
                {
                    "name": name,
                    "google_calendar_id": p["google_calendar_id"],
                    "caldav_calendar_name": p["caldav_calendar_name"],
                    "direction": direction,
                    "conflict_resolution": conflict,
                }
            )
        return normalized

    # Legacy single-pair form.
    cal_id = data["google"].get("calendar_id")
    cal_name = data["nextcloud"].get("calendar_name")
    if not cal_id or not cal_name:
        raise ConfigError(
            "Provide either 'calendar_pairs' or both google.calendar_id and "
            "nextcloud.calendar_name."
        )
    return [
        {
            "name": cal_name,
            "google_calendar_id": cal_id,
            "caldav_calendar_name": cal_name,
            "direction": default_direction,
            "conflict_resolution": default_conflict,
        }
    ]
