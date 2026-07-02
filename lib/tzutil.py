"""Timezone + working-hours helpers.

Adapted from internal utilities and rewritten standalone here to keep this
project sovereign and dependency-free:
  - country_to_timezone
  - in_quiet_hours

Pure functions, zero network, zero third-party deps.
"""

from __future__ import annotations

# ISO 3166-1 alpha-2 (upper) -> IANA timezone. Single-timezone jurisdictions only.
# Multi-timezone countries (US, RU, AU, CA, BR) are intentionally omitted: a
# country code alone cannot pin their zone, so callers fall back to a default.
COUNTRY_TZ: dict[str, str] = {
    # GCC
    "AE": "Asia/Dubai", "SA": "Asia/Riyadh", "KW": "Asia/Kuwait",
    "QA": "Asia/Qatar", "BH": "Asia/Bahrain", "OM": "Asia/Muscat",
    # Wider MENA
    "EG": "Africa/Cairo", "JO": "Asia/Amman", "LB": "Asia/Beirut",
    "IQ": "Asia/Baghdad", "SY": "Asia/Damascus", "YE": "Asia/Aden",
    "PS": "Asia/Hebron", "IL": "Asia/Jerusalem", "IR": "Asia/Tehran",
    "TR": "Europe/Istanbul", "LY": "Africa/Tripoli", "TN": "Africa/Tunis",
    "DZ": "Africa/Algiers", "MA": "Africa/Casablanca", "SD": "Africa/Khartoum",
    # Europe (single-tz)
    "GB": "Europe/London", "IE": "Europe/Dublin", "FR": "Europe/Paris",
    "DE": "Europe/Berlin", "NL": "Europe/Amsterdam", "BE": "Europe/Brussels",
    "IT": "Europe/Rome", "ES": "Europe/Madrid", "PT": "Europe/Lisbon",
    "CH": "Europe/Zurich", "AT": "Europe/Vienna", "SE": "Europe/Stockholm",
    "NO": "Europe/Oslo", "DK": "Europe/Copenhagen", "FI": "Europe/Helsinki",
    "PL": "Europe/Warsaw", "CZ": "Europe/Prague", "GR": "Europe/Athens",
    "RO": "Europe/Bucharest", "HU": "Europe/Budapest",
    # Asia / Africa (single-tz, common B2B)
    "IN": "Asia/Kolkata", "PK": "Asia/Karachi", "BD": "Asia/Dhaka",
    "LK": "Asia/Colombo", "SG": "Asia/Singapore", "MY": "Asia/Kuala_Lumpur",
    "PH": "Asia/Manila", "TH": "Asia/Bangkok", "VN": "Asia/Ho_Chi_Minh",
    "HK": "Asia/Hong_Kong", "JP": "Asia/Tokyo", "KR": "Asia/Seoul",
    "NG": "Africa/Lagos", "KE": "Africa/Nairobi", "ZA": "Africa/Johannesburg",
    "GH": "Africa/Accra",
}


def country_to_timezone(country_iso: str | None) -> str | None:
    """IANA timezone for an ISO-2 country code, or None if unknown/multi-tz.
    Case-insensitive; tolerates None/empty."""
    if not country_iso:
        return None
    return COUNTRY_TZ.get(country_iso.strip().upper())


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """True if ``hour`` (0-23, recipient-local) is inside the quiet window.

    Non-wrapping (start <= end): quiet when start <= hour < end.
    Wrapping (start > end, e.g. 21->8): quiet when hour >= start or hour < end.
    Degenerate (start == end): no quiet window.
    """
    hour %= 24
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def is_working_hours(hour: int, start: int = 9, end: int = 18) -> bool:
    """True if ``hour`` (0-23, local) is within working hours [start, end)."""
    return start <= (hour % 24) < end
