"""Robust-inference primitives + specification curve for relationship reports.

Baked (not left to codegen) because correct HAC standard errors, block bootstrap,
and FDR correction are exactly what ad-hoc analysis code gets subtly wrong, and the
report grader cannot verify a computed standard error from the number alone.

Typical use — calendar predictor → session returns, then a null signed-return curve:

    from nexustrade import stats
    frame = stats.next_session_frame(transits, asset_ret)  # collapse Fri/Sat/Sun → Mon
    curve = stats.spec_curve(
        predictor=frame['predictor'],
        asset_return=frame['asset_return'],
        baseline=88.0,
        regimes=[('Pre', '2025-03-30', '2026-02-27'), ('Escalation', '2026-02-28', '2026-06-16')],
    )
    brk = stats.mean_shift_break(transits)  # fail-able; argmax alone is not detection
    report.write(inputs={'statistics': {'spec_curve': curve, 'break': brk}, ...})
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence

import numpy as np
import pandas as pd

MIN_N = 20  # below this a Pearson r / p is not a supported inferential finding


def align_calendar_to_sessions(
    calendar: pd.Series,
    sessions: pd.DatetimeIndex,
    *,
    how: Literal["sum", "mean"] = "sum",
    lead: int = 1,
) -> pd.DataFrame:
    """Collapse non-session calendar days into each target session so each session appears once.

    Call this *before* ``spec_curve`` when the predictor is calendar-daily and the return
    series is session-indexed. Do **not** silently wrap ``spec_curve`` — callers that are
    already session-aligned must not double-collapse.

    Mapping:
      - ``lead=1`` (default): each calendar day maps to the next trading session *strictly
        after* that day (Fri/Sat/Sun → Monday; mid-week holiday days collapse onto the
        following open session). Usual next-day return join.
      - ``lead=0``: session on that day if it is a session, else the previous session
        (as-of). Already session-aligned series pass through with ``n_days_collapsed=1``.
      - ``lead>1``: the k-th session strictly after the calendar day.

    Returns a DataFrame indexed by unique ``session_date`` with columns
    ``predictor``, ``session_date``, ``n_days_collapsed``. Raises if the session index
    is not unique after collapse (duplication remaining is a bug, not a silent join).
    """
    if how not in ("sum", "mean"):
        raise ValueError(f"how must be 'sum' or 'mean', got {how!r}")
    if lead < 0:
        raise ValueError(f"lead must be >= 0, got {lead}")

    cal = pd.Series(calendar).astype(float)
    if not isinstance(cal.index, pd.DatetimeIndex):
        cal.index = pd.to_datetime(cal.index)
    cal.index = cal.index.normalize()
    cal = cal[~cal.index.isna()].sort_index()

    sess = pd.DatetimeIndex(pd.to_datetime(sessions)).normalize().unique().sort_values()
    if len(sess) == 0:
        raise ValueError("sessions must be non-empty")

    sess_ns = sess.asi8
    cal_ns = cal.index.asi8
    n_sess = len(sess_ns)

    if lead == 0:
        # Same-day session if present, else previous session (as-of).
        pos = np.searchsorted(sess_ns, cal_ns, side="left")
        exact = (pos < n_sess) & (sess_ns[np.minimum(pos, n_sess - 1)] == cal_ns)
        asof = np.searchsorted(sess_ns, cal_ns, side="right") - 1
        target_i = np.where(exact, pos, asof)
    else:
        # First session strictly after calendar day, then advance lead-1 more.
        target_i = np.searchsorted(sess_ns, cal_ns, side="right") + (lead - 1)

    valid = (target_i >= 0) & (target_i < n_sess)
    if not np.any(valid):
        return pd.DataFrame(
            columns=["predictor", "session_date", "n_days_collapsed"],
        ).rename_axis("session_date")

    mapped = pd.DataFrame(
        {
            "predictor": cal.to_numpy()[valid],
            "session_date": sess[target_i[valid]],
        }
    )
    agg = "sum" if how == "sum" else "mean"
    grouped = mapped.groupby("session_date", sort=True)
    out = grouped["predictor"].agg(agg).to_frame("predictor")
    out["session_date"] = out.index
    out["n_days_collapsed"] = grouped.size().astype(int)
    out = out[["predictor", "session_date", "n_days_collapsed"]]

    if not out.index.is_unique:
        raise AssertionError(
            "align_calendar_to_sessions produced a non-unique session index; "
            "refusing to emit duplicated session rows"
        )
    if out.index.duplicated().any():
        raise AssertionError("duplicated session_date after collapse")
    return out


def next_session_frame(
    predictor: pd.Series,
    asset_return: pd.Series,
    *,
    how: Literal["sum", "mean"] = "sum",
    lead: int = 1,
) -> pd.DataFrame:
    """Align a calendar predictor to session returns (one row per session).

    Sugar over ``align_calendar_to_sessions`` + an inner join on the return index.
    Columns: ``predictor``, ``asset_return``, ``session_date``, ``n_days_collapsed``.
    """
    ret = pd.Series(asset_return).astype(float)
    if not isinstance(ret.index, pd.DatetimeIndex):
        ret.index = pd.to_datetime(ret.index)
    ret.index = ret.index.normalize()
    if ret.index.has_duplicates:
        raise ValueError("asset_return index must be unique session dates")

    aligned = align_calendar_to_sessions(predictor, pd.DatetimeIndex(ret.index), how=how, lead=lead)
    if aligned.empty:
        return pd.DataFrame(
            columns=["predictor", "asset_return", "session_date", "n_days_collapsed"]
        )
    out = aligned.copy()
    out["asset_return"] = ret.reindex(out.index).to_numpy()
    out = out.dropna(subset=["asset_return"])
    return out[["predictor", "asset_return", "session_date", "n_days_collapsed"]]


def mean_shift_break(
    series: pd.Series,
    *,
    step: int = 30,
    min_abs_shift: float | None = None,
    alpha: float = 0.05,
    n_perm: int = 500,
    seed: int = 12345,
    min_side: int = 30,
) -> dict[str, Any]:
    """Scan for a single mean-shift break with a fail-able detection criterion.

    Freehand argmax always returns a date — that is exploratory, not detection.
    This helper only sets ``detected=True`` when the best absolute mean-shift clears
    a criterion:

      - if ``min_abs_shift`` is set: ``score >= min_abs_shift`` (series units)
      - else (default): permutation p-value ``< alpha``

    Prefer this over hand-rolled argmax. If you scan freehand, set ``detected=false``
    and method ``exploratory_argmax`` in the report — do not claim a break.
    """
    s = pd.Series(series).astype(float).dropna()
    if not isinstance(s.index, pd.DatetimeIndex):
        s.index = pd.to_datetime(s.index)
    s = s.sort_index()
    values = s.to_numpy(dtype=float)
    n = int(values.size)
    empty: dict[str, Any] = {
        "detected": False,
        "dates": [],
        "method": "mean_shift_break",
        "score": None,
        "p": None,
        "exploratory": True,
        "criterion": None,
        "n": n,
    }
    if n < 2 * min_side + 1 or step < 1:
        empty["reason"] = f"n<{2 * min_side + 1} or step<1"
        return empty

    candidates = list(range(min_side, n - min_side + 1, step))
    if not candidates:
        empty["reason"] = "no candidate split points"
        return empty

    def _best_score(arr: np.ndarray) -> tuple[float, int]:
        best_score = -1.0
        best_i = candidates[0]
        for i in candidates:
            score = abs(float(arr[:i].mean() - arr[i:].mean()))
            if score > best_score:
                best_score = score
                best_i = i
        return best_score, best_i

    score, split_i = _best_score(values)
    break_date = s.index[split_i]
    # Inclusive end of pre-break / start of post-break for regime tables.
    dates = [str(pd.Timestamp(break_date).date())]

    if min_abs_shift is not None:
        detected = bool(score >= min_abs_shift)
        criterion = f"min_abs_shift>={min_abs_shift}"
        p_val: float | None = None
    else:
        rng = np.random.default_rng(seed)
        exceed = 0
        for _ in range(n_perm):
            perm = rng.permutation(values)
            perm_score, _ = _best_score(perm)
            if perm_score >= score:
                exceed += 1
        p_val = (1.0 + exceed) / (1.0 + n_perm)
        detected = bool(p_val < alpha)
        criterion = f"permutation_p<{alpha}"

    return {
        "detected": detected,
        # Only populate dates when the fail-able criterion clears; otherwise the
        # scan's best date lives in exploratory_argmax_date (not a claimed break).
        "dates": dates if detected else [],
        "method": "mean_shift_break",
        "score": float(score),
        "p": p_val,
        "exploratory": not detected,
        "criterion": criterion,
        "n": n,
        "split_index": int(split_i),
        "step": step,
        "min_side": min_side,
        "exploratory_argmax_date": dates[0],
    }


def newey_west_slope(x: Sequence[float], y: Sequence[float], maxlags: int = 5) -> dict[str, Any]:
    """OLS(y ~ x) with Newey-West (HAC) covariance. Returns slope, HAC se, t, p, n.

    HAC because autocorrelated residuals (crisis-period daily data) make plain OLS /
    Pearson p-values too small. `maxlags` is the Bartlett-kernel bandwidth.
    """
    import statsmodels.api as sm

    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    mask = np.isfinite(xa) & np.isfinite(ya)
    xa, ya = xa[mask], ya[mask]
    n = int(xa.size)
    if n < 3 or np.ptp(xa) == 0:
        return {"n": n, "slope": None, "hac_se": None, "t": None, "p": None}
    lags = max(1, min(maxlags, n - 2))
    model = sm.OLS(ya, sm.add_constant(xa)).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return {
        "n": n,
        "slope": float(model.params[1]),
        "hac_se": float(model.bse[1]),
        "t": float(model.tvalues[1]),
        "p": float(model.pvalues[1]),
        "maxlags": lags,
    }


def block_bootstrap_corr(
    x: Sequence[float],
    y: Sequence[float],
    block: int = 5,
    n_boot: int = 2000,
    seed: int = 12345,
) -> dict[str, Any]:
    """Circular moving-block bootstrap of Pearson r. Returns r, 95% CI, two-sided p.

    Preserves short-run autocorrelation the IID bootstrap would destroy. Deterministic
    for a fixed seed. Bootstrap p = share of resamples whose r is on the opposite side
    of 0 from the point estimate (doubled, two-sided), floored at 1/n_boot.
    """
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    mask = np.isfinite(xa) & np.isfinite(ya)
    xa, ya = xa[mask], ya[mask]
    n = int(xa.size)
    if n < 3 or np.ptp(xa) == 0 or np.ptp(ya) == 0:
        return {"n": n, "r": None, "ci_low": None, "ci_high": None, "p": None}
    r_hat = float(np.corrcoef(xa, ya)[0, 1])
    block = max(1, min(block, n))
    n_blocks = int(np.ceil(n / block))
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(n_boot, n_blocks))
    offsets = np.arange(block)
    boot_r = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        idx = ((starts[i][:, None] + offsets).ravel() % n)[:n]
        bx, by = xa[idx], ya[idx]
        if np.ptp(bx) == 0 or np.ptp(by) == 0:
            boot_r[i] = 0.0
        else:
            boot_r[i] = np.corrcoef(bx, by)[0, 1]
    ci_low, ci_high = np.percentile(boot_r, [2.5, 97.5])
    opposite = np.mean(boot_r <= 0) if r_hat > 0 else np.mean(boot_r >= 0)
    p = min(1.0, max(1.0 / n_boot, 2.0 * float(opposite)))
    return {
        "n": n,
        "r": r_hat,
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
        "p": p,
        "block": block,
        "n_boot": n_boot,
    }


def benjamini_hochberg(pvalues: Sequence[float], alpha: float = 0.05) -> dict[str, Any]:
    """Benjamini-Hochberg FDR across a family of p-values. None entries are ignored.

    Returns per-p adjusted values (None where input was None) and the reject mask, so a
    spec curve can mark which cells survive multiple testing rather than raw p < 0.05.
    """
    from statsmodels.stats.multitest import multipletests

    idx = [i for i, p in enumerate(pvalues) if p is not None and np.isfinite(p)]
    adjusted: list[float | None] = [None] * len(pvalues)
    reject: list[bool] = [False] * len(pvalues)
    if not idx:
        return {"adjusted": adjusted, "reject": reject, "n_reject": 0, "n_tested": 0, "alpha": alpha}
    rej, p_adj, _, _ = multipletests([pvalues[i] for i in idx], alpha=alpha, method="fdr_bh")
    for k, i in enumerate(idx):
        adjusted[i] = float(p_adj[k])
        reject[i] = bool(rej[k])
    return {
        "adjusted": adjusted,
        "reject": reject,
        "n_reject": int(sum(reject)),
        "n_tested": len(idx),
        "alpha": alpha,
    }


def _adf_nonstationary(series: np.ndarray) -> bool | None:
    """True when an ADF test fails to reject a unit root (likely non-stationary)."""
    s = series[np.isfinite(series)]
    if s.size < 10 or np.ptp(s) == 0:
        return None
    try:
        from statsmodels.tsa.stattools import adfuller

        return bool(adfuller(s, autolag="AIC")[1] > 0.05)
    except Exception:
        return None


def _predictor_transforms(x: pd.Series, baseline: float | None) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {"level": x, "diff": x.diff()}
    if (x > 0).all():
        out["dlog"] = np.log(x).diff()
    if baseline:
        out["basedev"] = x / baseline - 1.0
    return out


def _target_transforms(y: pd.Series, vol_window: int) -> dict[str, pd.Series]:
    return {
        "signed_return": y,
        "abs_return": y.abs(),
        "realized_vol": y.rolling(vol_window).std(),
    }


def spec_curve(
    predictor: pd.Series,
    asset_return: pd.Series,
    *,
    baseline: float | None = None,
    regimes: Sequence[tuple[str, Any, Any]] | None = None,
    vol_window: int = 5,
    maxlags: int = 5,
    min_n: int = MIN_N,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Run a bounded, pre-declared specification curve and FDR-correct across it.

    Grid = {signed return, |return|, realized vol} targets x {level, diff, dlog,
    baseline-dev} predictor transforms x {full sample + each regime}. Every cell with
    n >= min_n gets a Pearson r and a Newey-West (HAC) p; the whole family is then
    BH-FDR corrected so a cell only 'survives' if it clears FDR, never raw p.

    This is EXPLORATORY: report the entire curve, headline nothing as confirmatory, and
    treat 'nothing survives' as the (event-driven) finding it is. `predictor` and
    `asset_return` must be date-indexed and already aligned to contemporaneous or the
    caller's intended lead/lag (no lookahead is introduced here).
    """
    x = pd.Series(predictor).astype(float)
    y = pd.Series(asset_return).astype(float)
    windows: list[tuple[str, Any, Any]] = [("full", None, None)]
    if regimes:
        windows.extend((str(lbl), a, b) for lbl, a, b in regimes)

    p_transforms = _predictor_transforms(x, baseline)
    specs: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for sample, start, end in windows:
        y_win = y.loc[start:end] if start is not None else y
        for p_name, p_series in p_transforms.items():
            px = p_series.loc[start:end] if start is not None else p_series
            stationarity = _adf_nonstationary(px.to_numpy()) if p_name == "level" else None
            for t_name, t_series in _target_transforms(y_win, vol_window).items():
                df = pd.concat([px, t_series], axis=1, join="inner").dropna()
                n = int(df.shape[0])
                label = f"{sample}:{p_name}->{t_name}"
                if n < min_n:
                    skipped.append({"spec": label, "n": n, "reason": f"n<{min_n}"})
                    continue
                xv, yv = df.iloc[:, 0].to_numpy(), df.iloc[:, 1].to_numpy()
                if np.ptp(xv) == 0 or np.ptp(yv) == 0:
                    skipped.append({"spec": label, "n": n, "reason": "degenerate (no variance)"})
                    continue
                hac = newey_west_slope(xv, yv, maxlags=maxlags)
                r = float(np.corrcoef(xv, yv)[0, 1])
                specs.append(
                    {
                        "spec": label,
                        "sample": sample,
                        "predictor": p_name,
                        "target": t_name,
                        "n": n,
                        "r": r,
                        "hac_p": hac["p"],
                        "likely_spurious_levels": bool(stationarity) if stationarity is not None else None,
                    }
                )

    fdr = benjamini_hochberg([s["hac_p"] for s in specs], alpha=alpha)
    for spec, adj, rej in zip(specs, fdr["adjusted"], fdr["reject"]):
        spec["hac_p_fdr"] = adj
        # A level-based full-sample correlation across a break is not a survivor even if
        # the number clears FDR — it is the classic spurious regression.
        spec["survives_fdr"] = bool(rej) and not spec.get("likely_spurious_levels")

    survivors = [s["spec"] for s in specs if s["survives_fdr"]]
    summary = (
        f"{len(specs)} specifications tested; {fdr['n_reject']} clear FDR "
        f"(alpha={alpha}); {len(survivors)} survive after excluding spurious level "
        f"correlations. Exploratory: any survivor needs out-of-sample confirmation."
    )
    if not survivors:
        summary += (
            " No specification shows daily-frequency signal — consistent with "
            "event-driven repricing rather than a daily-count relationship."
        )
    return {
        "method": "specification curve (HAC/Newey-West p, Benjamini-Hochberg FDR)",
        "vol_window": vol_window,
        "maxlags": maxlags,
        "min_n": min_n,
        "n_specs": len(specs),
        "n_fdr_reject": fdr["n_reject"],
        "survivors": survivors,
        "specs": specs,
        "skipped": skipped,
        "summary": summary,
        "exploratory": True,
    }
