"""Cross-system value mappings (colors, attendee status).

Kept pure and table-driven so they're trivially testable and easy to extend.
"""

from __future__ import annotations

from typing import Optional

# Google event colorId palette -> closest CSS3 color name (for iCal COLOR,
# per RFC 7986). Invertible so a color survives a Google -> CalDAV -> Google
# round-trip.
GOOGLE_COLOR_TO_CSS: dict[str, str] = {
    "1": "slateblue",       # Lavender
    "2": "mediumseagreen",  # Sage
    "3": "darkorchid",      # Grape
    "4": "salmon",          # Flamingo
    "5": "gold",            # Banana
    "6": "orangered",       # Tangerine
    "7": "deepskyblue",     # Peacock
    "8": "dimgray",         # Graphite
    "9": "royalblue",       # Blueberry
    "10": "green",          # Basil
    "11": "red",            # Tomato
}
CSS_TO_GOOGLE_COLOR: dict[str, str] = {v: k for k, v in GOOGLE_COLOR_TO_CSS.items()}

# Google responseStatus <-> iCalendar PARTSTAT
GOOGLE_STATUS_TO_PARTSTAT: dict[str, str] = {
    "needsAction": "NEEDS-ACTION",
    "declined": "DECLINED",
    "tentative": "TENTATIVE",
    "accepted": "ACCEPTED",
}
PARTSTAT_TO_GOOGLE_STATUS: dict[str, str] = {v: k for k, v in GOOGLE_STATUS_TO_PARTSTAT.items()}


def google_color_to_css(color_id: Optional[str]) -> Optional[str]:
    if not color_id:
        return None
    return GOOGLE_COLOR_TO_CSS.get(str(color_id))


def css_to_google_color(css: Optional[str]) -> Optional[str]:
    if not css:
        return None
    return CSS_TO_GOOGLE_COLOR.get(css.strip().lower())


def google_status_to_partstat(status: Optional[str]) -> str:
    return GOOGLE_STATUS_TO_PARTSTAT.get(status or "", "NEEDS-ACTION")


def partstat_to_google_status(partstat: Optional[str]) -> str:
    return PARTSTAT_TO_GOOGLE_STATUS.get((partstat or "").upper(), "needsAction")
