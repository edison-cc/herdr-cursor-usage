#!/usr/bin/env python3
"""Read this Mac's Cursor quota and print one JSON object.

The session token stays in a 0600 cookie jar for the curl calls. It is never
printed. Requests go only to cursor.com.
"""

import base64
import ctypes
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

DB_PATH = Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
PREFIX = "cursor-safe-storage:v1:"
UNREADABLE = "Could not decrypt the Cursor token — sign in to Cursor again"


class CursorError(Exception):
    """A known failure the dashboard can show."""


def fail(message):
    raise CursorError(message)


def safe_load(text):
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def epoch_ms(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 10 ** 11:
        return None
    return datetime.fromtimestamp(number / 1000, timezone.utc)


def parse_session_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            parsed = None
    if parsed is None:
        try:
            parsed = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def money(amount):
    if abs(amount - round(amount)) < 0.005:
        return f"${amount:.0f}"
    return f"${amount:.2f}"


def duration_phrase(seconds):
    if seconds <= 0:
        return ""
    total_min = int(seconds // 60)
    days, rem = divmod(total_min, 60 * 24)
    hours, minutes = divmod(rem, 60)
    if days > 0:
        parts = [f"{days}d", f"{hours}h"]
        if minutes:
            parts.append(f"{minutes}m")
        return " ".join(parts)
    if hours > 0:
        return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m"
    return "<1m"


def format_when(moment):
    local = moment.astimezone()
    hour = int(local.strftime("%I"))
    return local.strftime(f"%a, %b {local.day}, {hour}:%M %p")


def display_plan(info, fallback):
    name = str((info or {}).get("planName") or "").strip()
    source = name or str(fallback or "unknown")
    words = []
    for word in source.replace("_", " ").replace("-", " ").split():
        plus = word.endswith("+")
        core = word[:-1] if plus else word
        words.append(core[:1].upper() + core[1:].lower() + ("+" if plus else ""))
    return " ".join(words) or "Unknown"


def numeric_field(mapping, key):
    if not isinstance(mapping, dict) or key not in mapping:
        return None
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def as_dollars(key, value):
    if "cent" in key.lower():
        return value / 100.0
    if isinstance(value, int) and abs(value) >= 100:
        return value / 100.0
    return float(value)


def dollars_left(mapping):
    """Remaining allowance, only when planUsage carries both a spend and a limit."""
    limit = None
    for key in ("limitCents", "includedLimitCents", "limit", "includedLimit"):
        raw = numeric_field(mapping, key)
        if raw is not None:
            limit = as_dollars(key, raw)
            break
    if limit is None or limit <= 0:
        return None
    for key in ("remainingCents", "remaining"):
        raw = numeric_field(mapping, key)
        if raw is not None:
            return max(0.0, as_dollars(key, raw))
    used = None
    for key in ("totalSpendCents", "includedSpendCents", "totalSpend", "includedSpend"):
        raw = numeric_field(mapping, key)
        if raw is not None:
            used = as_dollars(key, raw)
            break
    if used is None:
        return None
    return max(0.0, limit - used)


def opt_pct(mapping, key):
    value = mapping.get(key) if isinstance(mapping, dict) else None
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def date_from_key(key):
    if not isinstance(key, str):
        return ""
    tail = key.rsplit(".", 1)[-1]
    if len(tail) == 10 and tail[4] == "-" and tail[7] == "-":
        return tail
    return ""


PRODUCT_LABELS = {
    "grok-bot": "Grok Bot",
    "cursor": "Cursor",
    "grok": "Grok",
    "api": "API",
    "other": "Other",
}
PRODUCT_ORDER = ["grok-bot", "cursor", "grok", "api", "other"]


def display_percent(value):
    """The integer the dashboard prints. Anything under 1% still shows as 1."""
    if value is None:
        return None
    bounded = min(100.0, max(0.0, float(value)))
    if 0 < bounded < 1:
        return 1
    return int(bounded)


def month_day(moment):
    if moment is None:
        return ""
    local = moment.astimezone()
    return local.strftime("%b") + f" {local.day}"


def usage_split(percent_used, rows):
    """Share the displayed percent across products, largest remainder first."""
    grouped = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cents = numeric_field(row, "cents")
        if cents is None or cents <= 0:
            continue
        product = str(row.get("product") or "other")
        grouped[product] = grouped.get(product, 0.0) + cents
    total = sum(grouped.values())
    shown = display_percent(percent_used)
    if not shown or total <= 0:
        return []
    parts = []
    for product, cents in grouped.items():
        exact = cents / total * shown
        parts.append({
            "product": product,
            "percent": int(exact),
            "remainder": exact - int(exact),
        })
    leftover = shown - sum(part["percent"] for part in parts)
    for part in sorted(parts, key=lambda item: -item["remainder"]):
        if leftover <= 0:
            break
        part["percent"] += 1
        leftover -= 1

    def sort_key(part):
        if part["product"] == "other":
            return (1, 0, 0)
        try:
            index = PRODUCT_ORDER.index(part["product"])
        except ValueError:
            index = len(PRODUCT_ORDER)
        return (0, -part["percent"], index)

    return [
        {
            "product": part["product"],
            "label": PRODUCT_LABELS.get(part["product"], part["product"].replace("-", " ").title()),
            "percent": part["percent"],
        }
        for part in sorted(parts, key=sort_key)
        if part["percent"] > 0
    ]


def compact_tokens(count):
    count = max(0, int(count))
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}B"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 10_000:
        return f"{count / 1_000:.1f}K"
    return f"{count:,}"


def spend_text(dollars):
    if abs(dollars) < 0.005:
        return "$0"
    return money(dollars)


def whole_number(value):
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def product_rank(product):
    try:
        return PRODUCT_ORDER.index(product)
    except ValueError:
        return len(PRODUCT_ORDER)


def daily_tokens(payload, start, end):
    """Tokens per day for the current month, grouped the way the dashboard chart is."""
    if not isinstance(payload, dict) or payload.get("availability") == "identity_unlinked":
        return None
    series = payload.get("series")
    if not isinstance(series, list) or not series or start is None or end is None or end < start:
        return None
    by_day = {}
    totals = {}
    included = {}
    spend = {}
    for item in series:
        if not isinstance(item, dict):
            continue
        product = str((item.get("dimensions") or {}).get("product") or "other")
        points = item.get("points") if isinstance(item.get("points"), list) else []
        point_tokens = 0
        point_included = 0
        point_spend = 0.0
        for point in points:
            if not isinstance(point, dict):
                continue
            when = parse_session_date(str(point.get("dayStartTime") or ""))
            tokens = whole_number(point.get("tokens"))
            point_tokens += tokens
            point_included += whole_number(point.get("includedTokens"))
            try:
                point_spend += float(point.get("contractedCost") or 0)
            except (TypeError, ValueError):
                pass
            if when is None:
                continue
            day = when.astimezone(timezone.utc).date().isoformat()
            bucket = by_day.setdefault(day, {})
            bucket[product] = bucket.get(product, 0) + tokens
        totals[product] = totals.get(product, 0) + (whole_number(item.get("totalTokens")) or point_tokens)
        included[product] = included.get(product, 0) + (whole_number(item.get("totalIncludedTokens")) or point_included)
        try:
            series_spend = float(item.get("totalContractedCost") or 0)
        except (TypeError, ValueError):
            series_spend = 0.0
        spend[product] = spend.get(product, 0.0) + (series_spend or point_spend)

    order = sorted(totals, key=lambda product: (product_rank(product), -totals[product]))
    table = sorted(totals, key=lambda product: (-totals[product], product_rank(product)))
    days = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        bucket = by_day.get(key, {})
        days.append({
            "date": key,
            "parts": [{"product": product, "tokens": bucket.get(product, 0)} for product in order],
        })
        cursor += timedelta(days=1)
    grand = whole_number(payload.get("totalTokens")) or sum(totals.values())
    included_total = whole_number(payload.get("totalIncludedTokens")) or sum(included.values())
    return {
        "total": compact_tokens(grand),
        "included": compact_tokens(included_total),
        "products": [
            {
                "product": product,
                "label": PRODUCT_LABELS.get(product, product.replace("-", " ").title()),
                "tokens": compact_tokens(totals[product]),
                "spend": spend_text(spend.get(product, 0.0)),
            }
            for product in table
        ],
        "days": days,
    }


def edits_from_analytics(payload):
    """Accepted line edits, oldest first. Tab is completion count, matching the dashboard toggle."""
    days = payload.get("dailyMetrics") if isinstance(payload, dict) else None
    items = []
    for day in days or []:
        if not isinstance(day, dict):
            continue
        when = epoch_ms(day.get("date"))
        if when is None:
            continue
        try:
            added = int(day.get("acceptedLinesAdded") or 0)
            deleted = int(day.get("acceptedLinesDeleted") or 0)
            tab = int(day.get("totalTabsAccepted") or 0)
        except (TypeError, ValueError):
            continue
        lines = added + deleted
        items.append({
            "date": when.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            "all": lines,
            "tab": tab,
            "agent": lines,
        })
    items.sort(key=lambda item: item["date"])
    return items


def edits_from_rows(rows):
    """Local fallback when the analytics call has no days. Oldest first."""
    items = []
    for row in rows or []:
        key = ""
        payload = row
        if isinstance(row, (list, tuple)) and len(row) == 2:
            key, payload = row
        item = payload if isinstance(payload, dict) else safe_load(payload)
        if not isinstance(item, dict):
            continue
        try:
            tab = int(item.get("tabSuggestedLines") or 0)
            agent = int(item.get("composerSuggestedLines") or 0)
        except (TypeError, ValueError):
            continue
        date = str(item.get("date") or "") or date_from_key(str(key) if key else "")
        items.append({"date": date, "all": tab + agent, "tab": tab, "agent": agent})
    items.reverse()
    return items


def build_report(
    usage,
    stripe,
    period,
    plan,
    spend,
    email="",
    daily_stats=None,
    first_session="",
    recent_commit=None,
    usage_status=None,
    analytics=None,
    daily_usage=None,
    now=None,
):
    """Turn dashboard payloads into the cards the mod renders."""
    usage = usage if isinstance(usage, dict) else {}
    stripe = stripe if isinstance(stripe, dict) else {}
    period = period if isinstance(period, dict) else {}
    plan_info = (plan or {}).get("planInfo") or {}
    if not isinstance(plan_info, dict):
        plan_info = {}
    spend_data = spend if isinstance(spend, dict) else {}
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    gpt4 = usage.get("gpt-4") or {}
    used = gpt4.get("numRequests") or 0
    total = gpt4.get("maxRequestUsage") or 0
    tokens = gpt4.get("numTokens") or 0
    start_str = usage.get("startOfMonth") or ""
    membership = stripe.get("membershipType", "unknown")

    members = spend_data.get("teamMemberSpend") or []
    me = next((member for member in members if str(member.get("email", "")).lower() == email.lower()), None)
    spend_key = "spendCents" if total else "overallSpendCents"
    has_spend = False
    spend_cents = 0
    limit_dollars = 0
    if me and (spend_key in me or not total):
        has_spend = True
        spend_cents = me.get(spend_key, 0) or 0
        limit_dollars = me.get("effectivePerUserLimitDollars", me.get("hardLimitOverrideDollars", 0)) or 0

    spend_dollars = spend_cents / 100
    quota_reached = total > 0 and (used >= total or (has_spend and spend_cents > 0))
    display_used = total if quota_reached else used
    req_pct = (display_used / total * 100) if total else 0
    spend_pct = (spend_dollars / limit_dollars * 100) if limit_dollars else 0

    plan_usage = period.get("planUsage") or {}
    included_pct = plan_usage.get("totalPercentUsed")
    if included_pct is None:
        included_pct = plan_usage.get("autoPercentUsed")
    try:
        included_pct = float(included_pct) if included_pct is not None else None
    except (TypeError, ValueError):
        included_pct = None

    status = usage_status if isinstance(usage_status, dict) else {}
    weekly = status.get("weeklyIncludedUsage") if status.get("availability") == "available" else None
    weekly = weekly if isinstance(weekly, dict) else {}
    weekly_metered = (status.get("weeklyIncludedStatus") or {}).get("status") == "metered"
    weekly_used = numeric_field(weekly, "usedCents")
    weekly_budget = numeric_field(weekly, "budgetCents")
    if weekly_metered and weekly_budget and weekly_used is not None:
        included_pct = 100.0 * weekly_used / weekly_budget

    period_start = epoch_ms(period.get("billingCycleStart"))
    period_end = epoch_ms(period.get("billingCycleEnd")) or epoch_ms(plan_info.get("includedUsageResetsAt"))
    if weekly_metered and weekly_budget:
        period_start = parse_session_date(str(weekly.get("periodStart") or "")) or period_start
        period_end = parse_session_date(str(weekly.get("resetsAt") or "")) or period_end
    usage_period = str(plan_info.get("includedUsagePeriod") or "")
    if weekly_metered and weekly_budget:
        period_label = "weekly"
    elif "WEEK" in usage_period:
        period_label = "weekly"
    elif "MONTH" in usage_period:
        period_label = "monthly"
    else:
        period_label = ""
    has_included = included_pct is not None and not total and not has_spend

    if has_included:
        usage_pct = included_pct
    elif has_spend:
        usage_pct = spend_pct
    elif total:
        usage_pct = req_pct
    else:
        usage_pct = None

    start_dt = None
    end_dt = None
    if has_included and period_start and period_end:
        start_dt = period_start
        end_dt = period_end
    elif start_str:
        try:
            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            if start_dt.month == 12:
                end_dt = start_dt.replace(year=start_dt.year + 1, month=1)
            else:
                end_dt = start_dt.replace(month=start_dt.month + 1)
        except ValueError:
            start_dt = None
            end_dt = None

    remaining_s = None
    countdown = ""
    if start_dt and end_dt:
        remaining_s = (end_dt - now).total_seconds()
        if remaining_s > 0:
            countdown = duration_phrase(remaining_s)

    pace = None
    projected = None
    elapsed_frac = None
    cycle_s = None
    if start_dt and end_dt and usage_pct is not None:
        elapsed_s = (now - start_dt).total_seconds()
        cycle_s = (end_dt - start_dt).total_seconds()
        if cycle_s > 0 and elapsed_s > 0:
            elapsed_frac = elapsed_s / cycle_s
            if elapsed_frac > 0:
                pace = usage_pct / (elapsed_frac * 100)
                projected = usage_pct / elapsed_frac

    forecast = ""
    pace_hot = False
    cycle_open = remaining_s is None or remaining_s > 0
    if usage_pct is not None and usage_pct >= 100:
        forecast = "Used up"
        pace_hot = True
    elif cycle_open and pace is not None and elapsed_frac is not None and elapsed_frac >= 0.10:
        if pace < 0.95:
            forecast = f"Under pace · on track for about {projected:.0f}%"
        elif pace < 1.2:
            forecast = "On pace"
        elif cycle_s:
            before = duration_phrase(cycle_s * (1 - 1 / pace))
            forecast = f"Ahead of pace · about {before} before reset" if before else "Ahead of pace"
            pace_hot = True

    room = ""
    if usage_pct is not None and usage_pct < 100 and remaining_s is not None and remaining_s > 0:
        remaining_pct = max(0.0, 100 - usage_pct)
        if remaining_s >= 86400:
            room = f"{remaining_pct / (remaining_s / 86400):.0f}% per day"
        elif remaining_s >= 3600:
            room = f"{remaining_pct / (remaining_s / 3600):.0f}% per hour"
        else:
            per_min = remaining_pct / max(remaining_s / 60, 1 / 60)
            room = f"{per_min:.0f}% per minute"

    if usage_pct is None:
        return {"ok": False, "error": "No Cursor usage to show"}

    included_dollars = dollars_left(plan_usage) if has_included else None
    auto_pct = opt_pct(plan_usage, "autoPercentUsed")
    api_pct = opt_pct(plan_usage, "apiPercentUsed")
    show_requests = bool(total) or (not has_spend and not has_included)

    if has_included:
        label = {"weekly": "Included this week", "monthly": "Included this month"}.get(period_label, "Included usage")
        value = f"{int(round(included_pct))}%"
        dollars_left_text = f"{money(included_dollars)} left" if included_dollars is not None else ""
    elif has_spend:
        label = "On-demand usage" if total else "Spend this cycle"
        value = money(spend_dollars)
        dollars_left_text = f"{money(max(0.0, limit_dollars - spend_dollars))} left" if limit_dollars else ""
    else:
        label = "Included requests"
        value = f"{display_used} / {total}"
        dollars_left_text = ""

    details = []
    if has_included and auto_pct is not None and api_pct is not None:
        details.append({"label": "AUTO", "value": f"{auto_pct:.0f}%"})
        details.append({"label": "API", "value": f"{api_pct:.0f}%"})
    if show_requests:
        details.append({"label": "TOKENS", "value": f"{int(tokens):,}"})

    last_commit = None
    commit = recent_commit if isinstance(recent_commit, dict) else safe_load(recent_commit or "")
    if isinstance(commit, dict) and "aiPercentage" in commit:
        try:
            last_commit = f"{float(commit['aiPercentage']):.0f}%"
        except (TypeError, ValueError):
            last_commit = None

    when_parts = []
    if end_dt is not None:
        when_parts.append(f"Resets {format_when(end_dt)}")
    if countdown:
        when_parts.append(countdown)
    if room:
        when_parts.append(f"{room} still open")
    elapsed = None if elapsed_frac is None else min(1.0, max(0.0, elapsed_frac))

    first_dt = parse_session_date(first_session)
    since = first_dt.astimezone().strftime("%b %Y") if first_dt is not None else ""

    included = None
    if weekly_metered and weekly_budget and usage_pct is not None and has_included:
        shown = display_percent(usage_pct)
        included = {
            "plan": display_plan(plan_info, membership),
            "percent": shown,
            "precise": usage_pct,
            "segments": usage_split(usage_pct, status.get("weeklyIncludedUsageByProduct")),
            "period": "Weekly included usage",
            "resets": f"Resets {month_day(end_dt)}" if end_dt is not None else "",
            "pace": forecast,
            "paceHot": pace_hot,
        }

    balance = None
    monthly = status.get("monthlyUsage") if status.get("availability") == "available" else None
    if isinstance(monthly, dict) and "usedCents" in monthly:
        used_cents = numeric_field(monthly, "usedCents") or 0
        limit_cents = numeric_field(monthly, "limitCents")
        turned_off = bool(monthly.get("additionalUsageOff")) or limit_cents == 0
        balance = {
            "used": f"${used_cents / 100:.2f}",
            "limit": "Not set" if limit_cents is None or turned_off else money(limit_cents / 100),
            "resets": f"Resets {month_day(parse_session_date(str(monthly.get('resetsAt') or '')))}".rstrip(),
        }
        if balance["resets"] == "Resets":
            balance["resets"] = ""

    tokens = None
    if isinstance(monthly, dict):
        month_start = parse_session_date(str(monthly.get("periodStart") or ""))
        month_end = parse_session_date(str(monthly.get("resetsAt") or ""))
        if month_start is not None:
            last = now
            if month_end is not None and last >= month_end:
                last = month_end - timedelta(seconds=1)
            if last >= month_start:
                tokens = daily_tokens(
                    daily_usage,
                    month_start.astimezone(timezone.utc).date(),
                    last.astimezone(timezone.utc).date(),
                )
        if tokens is not None:
            credit_cents = numeric_field(monthly, "creditsUsedCents")
            tokens["credit"] = "$0" if not credit_cents else money(credit_cents / 100)

    cloud_edits = edits_from_analytics(analytics)
    edits = cloud_edits or edits_from_rows(daily_stats)

    return {
        "ok": True,
        "error": None,
        "account": {
            "plan": display_plan(plan_info, membership),
            "email": email,
            "since": since,
        },
        "meter": {
            "label": label,
            "value": value,
            "dollarsLeft": dollars_left_text,
            "percentUsed": usage_pct,
            "elapsed": elapsed,
            "blocked": pace_hot,
            "when": " · ".join(when_parts),
            "pace": forecast,
            "paceHot": pace_hot,
        },
        "details": details,
        "lastCommit": last_commit,
        "included": included,
        "balance": balance,
        "tokens": tokens,
        "edits": edits,
    }


def decrypt_token(stored):
    token = stored.strip()
    if not token.startswith(PREFIX):
        return token
    try:
        blob = base64.b64decode(token[len(PREFIX):], validate=True)
    except ValueError:
        fail(UNREADABLE)
    if blob[:3] != b"v10":
        fail(UNREADABLE)
    ciphertext = blob[3:]
    cccrypt = ctypes.CDLL("/usr/lib/libSystem.B.dylib").CCCrypt
    cccrypt.restype = ctypes.c_int32
    cccrypt.argtypes = [ctypes.c_uint32] * 3 + [
        ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    ]
    found = denied = False
    for account in ("Cursor", "Cursor Key"):
        result = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", "Cursor Safe Storage", "-a", account],
            capture_output=True,
        )
        if result.returncode == 44:
            continue
        if result.returncode != 0:
            denied = True
            continue
        found = True
        key = hashlib.pbkdf2_hmac("sha1", result.stdout.rstrip(b"\n"), b"saltysalt", 1003, 16)
        out = ctypes.create_string_buffer(len(ciphertext))
        moved = ctypes.c_size_t()
        if cccrypt(1, 0, 1, key, 16, b" " * 16, ciphertext, len(ciphertext), out, len(out), ctypes.byref(moved)):
            continue
        try:
            plain = out.raw[:moved.value].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if plain.count(".") == 2:
            return plain
    if denied:
        fail("Keychain access to Cursor Safe Storage was denied — allow it, then Refresh")
    if not found:
        fail("Cursor Safe Storage is missing from the Keychain — sign in to Cursor again")
    fail(UNREADABLE)


def session_cookie(access_token):
    token = decrypt_token(access_token)
    parts = token.split(".")
    payload = parts[1] if len(parts) > 1 else ""
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, json.JSONDecodeError):
        claims = None
    sub = claims.get("sub") if isinstance(claims, dict) else None
    if not sub:
        fail("Invalid Cursor token — sign in to Cursor again")
    return f"{sub}::{token}"


def api_error_message(payload):
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if err == "not_authenticated":
        return "Cursor session expired — open Cursor and sign in again"
    if err:
        return f"Cursor API error: {err}"
    return None


def write_cookie_jar(cookie_value):
    handle = tempfile.NamedTemporaryFile(prefix="cursor-usage-cookie.", delete=False)
    path = Path(handle.name)
    os.chmod(path, 0o600)
    line = f".cursor.com\tTRUE\t/\tTRUE\t0\tWorkosCursorSessionToken\t{cookie_value}\n"
    handle.write(b"# Netscape HTTP Cookie File\n" + line.encode("utf-8"))
    handle.close()
    return path


def curl(cookie_jar, url, body=None, timeout=15):
    command = ["curl", "-sL", "--max-time", str(timeout)]
    if body is not None:
        command += [
            "-X", "POST",
            "-H", "Content-Type: application/json",
            "-H", "Origin: https://cursor.com",
            "-d", body,
        ]
    command += ["-b", str(cookie_jar), url]
    result = subprocess.run(command, capture_output=True)
    return result.stdout.decode("utf-8", "replace")


def read_db(db_path):
    if not db_path.is_file():
        fail("Cursor DB not found")
    uri = f"file:{db_path}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        fail(f"Cursor DB could not be read: {exc}")
    try:
        def one(key):
            row = connection.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
            return row[0] if row and row[0] is not None else ""

        daily = connection.execute(
            "SELECT key, value FROM ItemTable WHERE key LIKE 'aiCodeTracking.dailyStats.v1.5.%' ORDER BY key DESC LIMIT 14"
        ).fetchall()
        return {
            "token": one("cursorAuth/accessToken"),
            "email": one("cursorAuth/cachedEmail"),
            "first_session": one("telemetry.firstSessionDate"),
            "recent_commit": one("aiCodeTracking.recentCommit"),
            "daily": [(row[0], row[1]) for row in daily],
        }
    finally:
        connection.close()


def _wave(fetch, calls):
    """Run one wave together. Each call is (name, url, body, timeout)."""
    if not calls:
        return {}
    if len(calls) == 1:
        name, url, body, timeout = calls[0]
        return {name: fetch(name, url, body, timeout)}
    results = {}
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = {
            pool.submit(fetch, name, url, body, timeout): name
            for name, url, body, timeout in calls
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return results


def fetch_dashboard(fetch):
    """Ask Cursor for the dashboard payloads.

    `fetch(name, url, body, timeout)` returns response text. The first wave
    runs together. Team spend waits for stripe, and the token chart waits for
    usage-status, so those two start only after the first wave returns.
    """
    texts = _wave(fetch, [
        ("usage", "https://www.cursor.com/api/usage", None, 15),
        ("stripe", "https://www.cursor.com/api/auth/stripe", None, 15),
        ("period", "https://www.cursor.com/api/dashboard/get-current-period-usage", "{}", 30),
        ("plan", "https://www.cursor.com/api/dashboard/get-plan-info", "{}", 30),
        ("usage_status", "https://www.cursor.com/api/dashboard/v2/usage-status?scope=user", None, 30),
        ("analytics", "https://www.cursor.com/api/dashboard/get-user-analytics", "{}", 30),
    ])
    usage_text = texts["usage"]
    usage = safe_load(usage_text) if str(usage_text).strip() else {}
    message = api_error_message(usage)
    if message:
        fail(message)
    if not str(usage_text).strip() or not usage:
        fail("Cursor API did not respond")
    stripe = safe_load(texts["stripe"])
    period = safe_load(texts["period"])
    plan = safe_load(texts["plan"])
    usage_status = safe_load(texts["usage_status"])
    analytics = safe_load(texts["analytics"])
    wave2 = []
    team_id = stripe.get("teamId")
    if team_id not in (None, ""):
        wave2.append((
            "spend",
            "https://www.cursor.com/api/dashboard/get-team-spend",
            json.dumps({"teamId": team_id}),
            30,
        ))
    monthly = usage_status.get("monthlyUsage") if isinstance(usage_status, dict) else None
    if isinstance(monthly, dict) and monthly.get("periodStart") and monthly.get("resetsAt"):
        wave2.append((
            "daily",
            "https://www.cursor.com/api/dashboard/v2/query-daily-usage",
            json.dumps({
                "scope": "user",
                "startTime": monthly["periodStart"],
                "endTime": monthly["resetsAt"],
                "groupBy": ["product"],
                "topN": 5,
            }),
            30,
        ))
    texts2 = _wave(fetch, wave2)
    spend = safe_load(texts2["spend"]) if "spend" in texts2 else {}
    daily_usage = safe_load(texts2["daily"]) if "daily" in texts2 else {}
    return usage, stripe, period, plan, spend, usage_status, analytics, daily_usage


def fetch_live(db_path=DB_PATH):
    local = read_db(Path(db_path))
    if not str(local["token"]).strip():
        fail("No access token — sign in to Cursor")
    cookie_value = session_cookie(str(local["token"]))
    jar = write_cookie_jar(cookie_value)
    try:
        usage, stripe, period, plan, spend, usage_status, analytics, daily_usage = fetch_dashboard(
            lambda _name, url, body, timeout: curl(jar, url, body, timeout),
        )
    finally:
        jar.unlink(missing_ok=True)
    return build_report(
        usage,
        stripe,
        period,
        plan,
        spend,
        email=str(local["email"] or ""),
        daily_stats=local["daily"],
        first_session=str(local["first_session"] or ""),
        recent_commit=local["recent_commit"],
        usage_status=usage_status,
        analytics=analytics,
        daily_usage=daily_usage,
    )


def report_from_fixtures(directory):
    root = Path(directory)

    def load(name, default):
        path = root / name
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    local = load("local.json", {})
    now = parse_session_date(str(local.get("now") or "")) if isinstance(local, dict) else None
    return build_report(
        load("usage.json", {}),
        load("stripe.json", {}),
        load("period.json", {}),
        load("plan.json", {}),
        load("spend.json", {}),
        email=str(local.get("email") or "") if isinstance(local, dict) else "",
        daily_stats=local.get("dailyStats") if isinstance(local, dict) else None,
        first_session=str(local.get("firstSession") or "") if isinstance(local, dict) else "",
        recent_commit=local.get("recentCommit") if isinstance(local, dict) else None,
        usage_status=load("usage-status.json", {}),
        analytics=load("analytics.json", {}),
        daily_usage=load("daily.json", {}),
        now=now,
    )


def emit(report):
    json.dump(report, sys.stdout)
    sys.stdout.write("\n")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if "--fixtures" in argv:
            index = argv.index("--fixtures")
            if index + 1 >= len(argv):
                fail("--fixtures needs a directory")
            report = report_from_fixtures(argv[index + 1])
        else:
            report = fetch_live()
    except CursorError as exc:
        report = {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — the dashboard shows one line, never a traceback
        report = {"ok": False, "error": str(exc) or "Cursor usage failed"}
    emit(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
