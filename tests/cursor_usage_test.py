import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "mods" / "cursor-usage" / "cursor_usage.py"

spec = importlib.util.spec_from_file_location("cursor_usage", SCRIPT)
cursor_usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cursor_usage)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def ms(moment):
    return int(moment.timestamp() * 1000)


class MeterTests(unittest.TestCase):
    def test_included_weekly_uses_actual_percent_and_under_pace(self):
        start = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 11, 13, 0, 0, tzinfo=timezone.utc)
        report = cursor_usage.build_report(
            {"gpt-4": {"numRequests": 0, "maxRequestUsage": None, "numTokens": 0}},
            {"membershipType": "premium"},
            {
                "billingCycleStart": ms(start),
                "billingCycleEnd": ms(end),
                "planUsage": {
                    "totalPercentUsed": 20,
                    "autoPercentUsed": 12,
                    "apiPercentUsed": 8,
                    "limitCents": 2000,
                    "remainingCents": 1600,
                },
            },
            {"planInfo": {"planName": "Premium", "includedUsagePeriod": "INCLUDED_USAGE_PERIOD_WEEKLY"}},
            {},
            email="ada@example.com",
            daily_stats=[
                {"date": "2026-11-09", "tabSuggestedLines": 1, "composerSuggestedLines": 2},
                ("aiCodeTracking.dailyStats.v1.5.2026-11-08", {"tabSuggestedLines": 0, "composerSuggestedLines": 4}),
            ],
            first_session="2026-01-15T00:00:00Z",
            recent_commit={"aiPercentage": "80.00"},
            now=NOW,
        )
        self.assertTrue(report["ok"])
        meter = report["meter"]
        self.assertEqual(meter["label"], "Included this week")
        self.assertEqual(meter["value"], "20%")
        self.assertEqual(meter["dollarsLeft"], "$16 left")
        self.assertEqual(meter["percentUsed"], 20)
        self.assertAlmostEqual(meter["elapsed"], 0.5)
        self.assertFalse(meter["blocked"])
        self.assertIn("· 3d 12h", meter["when"])
        self.assertIn("23% per day still open", meter["when"])
        self.assertEqual(meter["pace"], "Under pace · on track for about 40%")
        self.assertFalse(meter["paceHot"])
        self.assertNotIn("stats", meter)
        self.assertEqual(report["details"], [
            {"label": "AUTO", "value": "12%"},
            {"label": "API", "value": "8%"},
        ])
        self.assertEqual(report["lastCommit"], "80%")
        self.assertIsNone(report["included"])
        self.assertIsNone(report["balance"])
        self.assertEqual(report["edits"], [
            {"date": "2026-11-08", "all": 4, "tab": 0, "agent": 4},
            {"date": "2026-11-09", "all": 3, "tab": 1, "agent": 2},
        ])
        self.assertEqual(report["account"]["plan"], "Premium")
        self.assertEqual(report["account"]["email"], "ada@example.com")
        self.assertEqual(report["account"]["since"], "Jan 2026")

    def test_request_quota_colors_from_the_projection(self):
        report = cursor_usage.build_report(
            {
                "gpt-4": {"numRequests": 120, "maxRequestUsage": 500, "numTokens": 1000000},
                "startOfMonth": "2026-11-01T00:00:00Z",
            },
            {"membershipType": "pro"},
            {},
            {},
            {},
            now=datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc),
        )
        meter = report["meter"]
        self.assertEqual(meter["label"], "Included requests")
        self.assertEqual(meter["value"], "120 / 500")
        self.assertEqual(meter["dollarsLeft"], "")
        self.assertEqual(meter["percentUsed"], 24)
        self.assertFalse(meter["blocked"])
        self.assertEqual(meter["pace"], "Under pace · on track for about 80%")
        self.assertFalse(meter["paceHot"])
        self.assertEqual(report["details"], [{"label": "TOKENS", "value": "1,000,000"}])
        self.assertEqual(report["edits"], [])

    def test_team_spend_uses_the_signed_in_email(self):
        report = cursor_usage.build_report(
            {
                "gpt-4": {"numRequests": 0, "maxRequestUsage": None, "numTokens": 0},
                "startOfMonth": "2026-11-09T00:00:00Z",
            },
            {"membershipType": "enterprise", "teamId": 7},
            {},
            {"planInfo": {"planName": "Enterprise"}},
            {
                "teamMemberSpend": [
                    {"email": "other@example.com", "overallSpendCents": 99900, "effectivePerUserLimitDollars": 1000},
                    {"email": "Ada@Example.com", "overallSpendCents": 8200, "effectivePerUserLimitDollars": 1000},
                ]
            },
            email="ada@example.com",
            now=NOW,
        )
        meter = report["meter"]
        self.assertEqual(meter["label"], "Spend this cycle")
        self.assertEqual(meter["value"], "$82")
        self.assertEqual(meter["dollarsLeft"], "$918 left")
        self.assertAlmostEqual(meter["percentUsed"], 8.2)
        self.assertLess(meter["elapsed"], 0.10)
        self.assertFalse(meter["blocked"])
        self.assertEqual(meter["pace"], "")
        self.assertIn("per day still open", meter["when"])
        self.assertEqual(report["details"], [])

    def test_usage_status_splits_the_week_like_the_dashboard(self):
        report = cursor_usage.build_report(
            {"gpt-4": {"numRequests": 0, "maxRequestUsage": None, "numTokens": 0}},
            {"membershipType": "premium"},
            {"planUsage": {"totalPercentUsed": 18}},
            {"planInfo": {"planName": "Premium", "includedUsagePeriod": "INCLUDED_USAGE_PERIOD_WEEKLY"}},
            {},
            usage_status={
                "availability": "available",
                "weeklyIncludedStatus": {"status": "metered"},
                "weeklyIncludedUsage": {
                    "usedCents": 886,
                    "budgetCents": 4615,
                    "periodStart": "2026-11-07T00:07:08.817Z",
                    "resetsAt": "2026-11-14T00:07:08.817Z",
                },
                "weeklyIncludedUsageByProduct": [
                    {"product": "grok-bot", "cents": 523},
                    {"product": "cursor", "cents": 382},
                ],
                "monthlyUsage": {
                    "usedCents": 0,
                    "limitCents": None,
                    "additionalUsageOff": False,
                    "resetsAt": "2026-12-01T00:00:00.000Z",
                },
            },
            analytics={
                "dailyMetrics": [
                    {"date": 1793750400000, "acceptedLinesAdded": 245, "acceptedLinesDeleted": 73, "totalTabsAccepted": 0},
                    {"date": 1794268800000, "acceptedLinesAdded": 1729, "acceptedLinesDeleted": 168, "totalTabsAccepted": 2},
                ]
            },
            now=datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc),
        )
        included = report["included"]
        self.assertEqual(included["plan"], "Premium")
        self.assertEqual(included["percent"], 19)
        self.assertEqual(included["segments"], [
            {"product": "grok-bot", "label": "Grok Bot", "percent": 11},
            {"product": "cursor", "label": "Cursor", "percent": 8},
        ])
        self.assertEqual(included["period"], "Weekly included usage")
        self.assertEqual(
            included["resets"],
            "Resets " + cursor_usage.month_day(datetime(2026, 11, 14, 0, 7, tzinfo=timezone.utc)),
        )
        self.assertIn("Under pace", included["pace"])
        self.assertEqual(report["balance"]["used"], "$0.00")
        self.assertEqual(report["balance"]["limit"], "Not set")
        self.assertEqual(
            report["balance"]["resets"],
            "Resets " + cursor_usage.month_day(datetime(2026, 12, 1, tzinfo=timezone.utc)),
        )
        self.assertEqual(report["edits"], [
            {"date": "2026-11-04", "all": 318, "tab": 0, "agent": 318},
            {"date": "2026-11-10", "all": 1897, "tab": 2, "agent": 1897},
        ])
        self.assertIsNone(report["tokens"])

    def test_daily_tokens_follow_the_month_and_product(self):
        report = cursor_usage.build_report(
            {"gpt-4": {"numRequests": 0, "maxRequestUsage": None, "numTokens": 0}},
            {"membershipType": "premium"},
            {"planUsage": {"totalPercentUsed": 10}},
            {"planInfo": {"planName": "Premium", "includedUsagePeriod": "INCLUDED_USAGE_PERIOD_WEEKLY"}},
            {},
            usage_status={
                "availability": "available",
                "weeklyIncludedStatus": {"status": "metered"},
                "weeklyIncludedUsage": {
                    "usedCents": 100,
                    "budgetCents": 1000,
                    "periodStart": "2026-11-07T00:00:00.000Z",
                    "resetsAt": "2026-11-14T00:00:00.000Z",
                },
                "monthlyUsage": {
                    "usedCents": 0,
                    "limitCents": None,
                    "creditsUsedCents": 0,
                    "periodStart": "2026-11-01T00:00:00.000Z",
                    "resetsAt": "2026-12-01T00:00:00.000Z",
                },
            },
            daily_usage={
                "totalTokens": 3000000,
                "totalIncludedTokens": 3000000,
                "series": [
                    {
                        "dimensions": {"product": "cursor"},
                        "totalTokens": 1000000,
                        "totalIncludedTokens": 1000000,
                        "totalContractedCost": "1.5",
                        "points": [{
                            "dayStartTime": "2026-11-02T00:00:00.000Z",
                            "tokens": 1000000,
                            "includedTokens": 1000000,
                            "contractedCost": "1.5",
                        }],
                    },
                    {
                        "dimensions": {"product": "grok-bot"},
                        "totalTokens": 2000000,
                        "totalIncludedTokens": 2000000,
                        "totalContractedCost": "0",
                        "points": [{
                            "dayStartTime": "2026-11-02T00:00:00.000Z",
                            "tokens": 2000000,
                            "includedTokens": 2000000,
                            "contractedCost": "0",
                        }],
                    },
                ],
            },
            now=datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc),
        )
        tokens = report["tokens"]
        self.assertEqual(tokens["total"], "3.0M")
        self.assertEqual(tokens["included"], "3.0M")
        self.assertEqual(tokens["credit"], "$0")
        self.assertEqual(tokens["products"], [
            {"product": "grok-bot", "label": "Grok Bot", "tokens": "2.0M", "spend": "$0"},
            {"product": "cursor", "label": "Cursor", "tokens": "1.0M", "spend": "$1.50"},
        ])
        self.assertEqual([day["date"] for day in tokens["days"]], ["2026-11-01", "2026-11-02", "2026-11-03"])
        self.assertEqual(tokens["days"][1]["parts"], [
            {"product": "grok-bot", "tokens": 2000000},
            {"product": "cursor", "tokens": 1000000},
        ])
        self.assertEqual(tokens["days"][0]["parts"][0]["tokens"], 0)

    def test_fixtures_cli_does_not_read_the_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "usage.json").write_text(json.dumps({
                "gpt-4": {"numRequests": 10, "maxRequestUsage": 100, "numTokens": 50},
                "startOfMonth": "2026-11-01T00:00:00Z",
            }), encoding="utf-8")
            (root / "stripe.json").write_text("{}", encoding="utf-8")
            (root / "local.json").write_text(json.dumps({"now": "2026-11-02T00:00:00Z"}), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--fixtures", directory],
                check=True,
                capture_output=True,
                text=True,
            )
        report = json.loads(result.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["meter"]["value"], "10 / 100")
        self.assertNotIn("WorkosCursorSessionToken", result.stdout)
        self.assertNotIn("eyJ", result.stdout)


if __name__ == "__main__":
    unittest.main()
