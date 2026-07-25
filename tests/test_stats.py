"""Unit tests for nexustrade.stats (run without a sandbox / without duckdb)."""

from __future__ import annotations

import unittest

# `stats` is the optional [stats] extra; the base install must stay stdlib-only.
try:
    import numpy as np
    import pandas as pd

    from nexustrade import stats
except ImportError as error:  # pragma: no cover - exercised by the base install
    raise unittest.SkipTest(
        f"nexustrade[stats] extra not installed: {error}"
    ) from error


class NeweyWestTests(unittest.TestCase):
    def test_recovers_known_slope(self) -> None:
        rng = np.random.default_rng(1)
        x = np.linspace(0, 10, 200)
        y = 2.0 * x + rng.normal(0, 1, 200)
        res = stats.newey_west_slope(x, y, maxlags=5)
        self.assertAlmostEqual(res["slope"], 2.0, delta=0.2)
        self.assertLess(res["p"], 0.01)
        self.assertEqual(res["n"], 200)

    def test_degenerate_predictor_is_safe(self) -> None:
        res = stats.newey_west_slope([1, 1, 1, 1], [1, 2, 3, 4])
        self.assertIsNone(res["p"])


class BootstrapAndFDRTests(unittest.TestCase):
    def test_block_bootstrap_is_deterministic(self) -> None:
        rng = np.random.default_rng(2)
        x = rng.normal(0, 1, 120)
        y = x + rng.normal(0, 1, 120)
        a = stats.block_bootstrap_corr(x, y, seed=7)
        b = stats.block_bootstrap_corr(x, y, seed=7)
        self.assertEqual(a["r"], b["r"])
        self.assertEqual(a["ci_low"], b["ci_low"])
        self.assertGreater(a["r"], 0.4)

    def test_bh_rejects_only_the_real_signal(self) -> None:
        # one true tiny p among many nulls near 1.0
        out = stats.benjamini_hochberg([0.0001, 0.6, 0.7, 0.8, 0.9])
        self.assertTrue(out["reject"][0])
        self.assertFalse(any(out["reject"][1:]))
        self.assertEqual(out["n_tested"], 5)

    def test_bh_ignores_none(self) -> None:
        out = stats.benjamini_hochberg([None, 0.001, None])
        self.assertEqual(out["n_tested"], 1)
        self.assertTrue(out["reject"][1])


class SpecCurveTests(unittest.TestCase):
    def _series(self):
        rng = np.random.default_rng(3)
        idx = pd.date_range("2025-01-01", periods=300, freq="D")
        x = pd.Series(rng.normal(50, 10, 300), index=idx)
        # signed return unrelated to x; volatility (|return|) scales with x → vol signal.
        ret = pd.Series(rng.normal(0, 1, 300) * (x.to_numpy() / 50.0), index=idx)
        return x, ret

    def test_finds_vol_signal_not_signed(self) -> None:
        x, ret = self._series()
        curve = stats.spec_curve(x, ret, vol_window=5)
        self.assertTrue(curve["exploratory"])
        self.assertGreater(curve["n_specs"], 0)
        by_spec = {s["spec"]: s for s in curve["specs"]}
        # signed-return on the level should be a null; abs_return should carry signal.
        self.assertIn("full:level->signed_return", by_spec)
        self.assertIn("full:level->abs_return", by_spec)
        self.assertGreater(
            by_spec["full:level->signed_return"]["hac_p"],
            by_spec["full:level->abs_return"]["hac_p"],
        )

    def test_reports_full_family_and_summary(self) -> None:
        x, ret = self._series()
        curve = stats.spec_curve(
            x, ret, regimes=[("A", "2025-01-01", "2025-05-01"), ("B", "2025-05-02", "2025-10-27")]
        )
        self.assertIn("specifications tested", curve["summary"])
        # every survivor must actually be in the specs list and clear FDR
        survivors = [s for s in curve["specs"] if s["survives_fdr"]]
        self.assertEqual(sorted(curve["survivors"]), sorted(s["spec"] for s in survivors))

    def test_low_n_is_skipped_not_correlated(self) -> None:
        rng = np.random.default_rng(4)
        idx = pd.date_range("2025-01-01", periods=10, freq="D")
        x = pd.Series(rng.normal(0, 1, 10), index=idx)
        ret = pd.Series(rng.normal(0, 1, 10), index=idx)
        curve = stats.spec_curve(x, ret)
        self.assertEqual(curve["n_specs"], 0)
        self.assertTrue(all(s["reason"].startswith("n<") for s in curve["skipped"]))


class AlignCalendarTests(unittest.TestCase):
    def test_fri_sat_sun_collapse_to_monday(self) -> None:
        # 2025-01-03 Fri, 04 Sat, 05 Sun → next session Mon 2025-01-06
        cal = pd.Series(
            [10.0, 3.0, 2.0, 1.0],
            index=pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05"]),
        )
        sessions = pd.DatetimeIndex(pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]))
        out = stats.align_calendar_to_sessions(cal, sessions, how="sum", lead=1)
        self.assertTrue(out.index.is_unique)
        # Thu 01-02 → Fri 01-03; Fri+Sat+Sun → Mon 01-06
        self.assertEqual(float(out.loc["2025-01-03", "predictor"]), 10.0)
        self.assertEqual(float(out.loc["2025-01-06", "predictor"]), 6.0)  # 3+2+1
        self.assertEqual(int(out.loc["2025-01-06", "n_days_collapsed"]), 3)

    def test_midweek_holiday_collapses_onto_next_open(self) -> None:
        # Wed 2025-01-01 holiday (absent from sessions); Tue+Wed → Thu
        cal = pd.Series(
            [5.0, 7.0, 4.0],
            index=pd.to_datetime(["2024-12-31", "2025-01-01", "2025-01-02"]),
        )
        sessions = pd.DatetimeIndex(pd.to_datetime(["2024-12-31", "2025-01-02", "2025-01-03"]))
        out = stats.align_calendar_to_sessions(cal, sessions, how="sum", lead=1)
        # Tue 12-31 → Thu 01-02; Wed holiday 01-01 → Thu 01-02; Thu 01-02 → Fri 01-03
        self.assertEqual(float(out.loc["2025-01-02", "predictor"]), 12.0)  # 5+7
        self.assertEqual(int(out.loc["2025-01-02", "n_days_collapsed"]), 2)
        self.assertEqual(float(out.loc["2025-01-03", "predictor"]), 4.0)

    def test_already_aligned_passthrough_lead0(self) -> None:
        idx = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
        cal = pd.Series([1.0, 2.0, 3.0], index=idx)
        out = stats.align_calendar_to_sessions(cal, pd.DatetimeIndex(idx), how="sum", lead=0)
        self.assertEqual(len(out), 3)
        self.assertTrue((out["n_days_collapsed"] == 1).all())
        self.assertEqual(list(out["predictor"]), [1.0, 2.0, 3.0])

    def test_how_mean_vs_sum(self) -> None:
        cal = pd.Series(
            [10.0, 20.0],
            index=pd.to_datetime(["2025-01-04", "2025-01-05"]),  # Sat+Sun → Mon
        )
        sessions = pd.DatetimeIndex(pd.to_datetime(["2025-01-03", "2025-01-06"]))
        summed = stats.align_calendar_to_sessions(cal, sessions, how="sum", lead=1)
        averaged = stats.align_calendar_to_sessions(cal, sessions, how="mean", lead=1)
        self.assertEqual(float(summed.loc["2025-01-06", "predictor"]), 30.0)
        self.assertEqual(float(averaged.loc["2025-01-06", "predictor"]), 15.0)

    def test_lead0_vs_lead1(self) -> None:
        cal = pd.Series([9.0], index=pd.to_datetime(["2025-01-03"]))  # Friday
        sessions = pd.DatetimeIndex(pd.to_datetime(["2025-01-03", "2025-01-06"]))
        same = stats.align_calendar_to_sessions(cal, sessions, lead=0)
        nxt = stats.align_calendar_to_sessions(cal, sessions, lead=1)
        self.assertEqual(same.index[0], pd.Timestamp("2025-01-03"))
        self.assertEqual(nxt.index[0], pd.Timestamp("2025-01-06"))

    def test_next_session_frame_unique_returns(self) -> None:
        cal = pd.Series(
            [1.0, 2.0, 3.0],
            index=pd.to_datetime(["2025-01-03", "2025-01-04", "2025-01-05"]),
        )
        ret = pd.Series(
            [0.01, 0.02],
            index=pd.to_datetime(["2025-01-03", "2025-01-06"]),
        )
        frame = stats.next_session_frame(cal, ret, how="sum", lead=1)
        self.assertEqual(len(frame), 1)
        self.assertEqual(float(frame.loc["2025-01-06", "predictor"]), 6.0)
        self.assertEqual(float(frame.loc["2025-01-06", "asset_return"]), 0.02)
        self.assertTrue(frame.index.is_unique)


class MeanShiftBreakTests(unittest.TestCase):
    def test_detects_large_shift_with_min_abs(self) -> None:
        idx = pd.date_range("2024-01-01", periods=200, freq="D")
        vals = np.concatenate([np.full(100, 0.0), np.full(100, 10.0)])
        s = pd.Series(vals, index=idx)
        out = stats.mean_shift_break(s, step=10, min_side=20, min_abs_shift=5.0, n_perm=50)
        self.assertTrue(out["detected"])
        self.assertEqual(len(out["dates"]), 1)
        self.assertFalse(out["exploratory"])
        self.assertGreaterEqual(out["score"], 5.0)

    def test_null_series_not_detected_by_permutation(self) -> None:
        rng = np.random.default_rng(11)
        idx = pd.date_range("2024-01-01", periods=200, freq="D")
        s = pd.Series(rng.normal(0, 1, 200), index=idx)
        out = stats.mean_shift_break(s, step=10, min_side=20, alpha=0.01, n_perm=100, seed=11)
        self.assertFalse(out["detected"])
        self.assertEqual(out["dates"], [])
        self.assertTrue(out["exploratory"])
        self.assertIsNotNone(out["exploratory_argmax_date"])

    def test_short_series_is_safe(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0], index=pd.date_range("2024-01-01", periods=3))
        out = stats.mean_shift_break(s)
        self.assertFalse(out["detected"])
        self.assertIsNone(out["score"])


if __name__ == "__main__":
    unittest.main()
