#!/usr/bin/env python3
"""Self-check for mu.py — smallest runnable check per Ponytail rule.

Exercises pure logic (day-total bucketing) against a mocked readings feed;
no network. Run: python3 test_mu.py
"""
import datetime as dt
import time
import unittest

import mu


class FakeReadings:
    def __init__(self, buckets):
        self.buckets = buckets  # {period:function: [(ts, val), ...]}

    def readings(self, rid, frm, to, period="P1D", function="sum"):
        return {"data": self.buckets.get((period, function), [])}


class DayTotalsTest(unittest.TestCase):
    def run_day_totals(self, buckets):
        fake = FakeReadings(buckets)
        orig = mu.readings
        mu._readings_bucket.__globals__["readings"] = fake.readings
        try:
            return mu._day_totals("test-resource")
        finally:
            mu._readings_bucket.__globals__["readings"] = orig

    def test_today_yesterday_and_sums(self):
        now = time.gmtime()
        today_mid = dt.datetime(now.tm_year, now.tm_mon, now.tm_mday,
                                tzinfo=dt.timezone.utc)
        yest_mid = today_mid - dt.timedelta(days=1)
        week_ago = today_mid - dt.timedelta(days=6)
        month_start = today_mid.replace(day=1)

        def ts(d):
            return int(d.timestamp())

        pts = [
            (ts(week_ago), 1.0),
            (ts(yest_mid), 2.0),
            (ts(today_mid), 3.5),
        ]
        if ts(month_start) < ts(week_ago):
            pass  # mid-month scenario below may add more; keep simple
        r = self.run_day_totals({("P1D", "sum"): pts})
        self.assertEqual(r["today"], 3.5)
        self.assertEqual(r["yesterday"], 2.0)
        # last7 includes all six+ days present in window
        self.assertAlmostEqual(r["last7_sum"], sum(v for t, v in pts
                                                   if t >= ts(week_ago)))
        # mtd = everything from month start inclusive
        expected_mtd = round(sum(v for t, v in sorted(pts)
                                 if t >= ts(month_start)), 3)
        self.assertEqual(r["mtd_sum"], expected_mtd)

    def test_empty_feed(self):
        r = self.run_day_totals({})
        self.assertIsNone(r["today"])
        self.assertIsNone(r["yesterday"])
        self.assertIsNone(r["last7_sum"])


if __name__ == "__main__":
    unittest.main()
