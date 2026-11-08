"""Alert rules. Each rule is a small independent function:

    rule(cur, prev, acfg, ctx) -> list[alert]

cur  : DataFrame, forecast rows of the chosen origin (<=40, columns of schema 1.0 + 'spot')
prev : DataFrame of the previous hourly origin, or None
acfg : config["alerts"] thresholds
ctx  : dict(glossary=Glossary, typical_width=float|None, width_q=float|None)

An alert is {id, rule, severity (info|watch|act), title, window{start,end}, message, review,
evidence{...}, drivers[{name,label,explain}]}.  Time tokens {t:ISO} in text are rendered by the UI in
local time or UTC.  To add a rule: write a function and decorate it with @rule("name").
"""
import numpy as np
import pandas as pd

SEV = {"info": 0, "watch": 1, "act": 2}
RULES = []

DISCLAIMER = "Plant capacity, costs and constraints are not in the open data."


def rule(name):
    def deco(fn):
        fn.rule_name = name
        RULES.append(fn)
        return fn
    return deco


# ----------------------------------------------------------------- helpers
def _t(ts):
    return "{t:%s}" % pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_runs(mask, min_len=1, max_gap=1):
    """Contiguous True runs (gaps of <= max_gap False merged). Returns [(i0, i1_inclusive)]."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    runs, s, p = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - p - 1 > max_gap:
            runs.append((s, p))
            s = i
        p = i
    runs.append((s, p))
    return [(a, b) for a, b in runs if (b - a + 1) >= min_len]


def _window(cur, a, b):
    st = cur["delivery_start_utc"].iloc[a]
    en = cur["delivery_start_utc"].iloc[b] + pd.Timedelta(minutes=15)
    return st, en


def _drivers(cur, a, b, ctx, k=4):
    counts = {}
    for s in cur["top_drivers"].iloc[a:b + 1]:
        for n in str(s).split(";"):
            n = n.strip()
            if n and n.lower() != "nan":
                counts[n] = counts.get(n, 0) + 1
    names = sorted(counts, key=lambda n: -counts[n])
    return ctx["glossary"].describe_many(names, k)


def _driver_sentence(drivers):
    if not drivers:
        return ""
    return "Drivers: %s." % ", ".join(d["label"] for d in drivers[:3])


def _uncertainty(cur, a, b, ctx):
    w = float((cur["p90"] - cur["p10"]).iloc[a:b + 1].mean())
    tw = ctx.get("typical_width")
    if tw and w >= 1.3 * tw:
        lvl = "high"
    elif tw and w <= 0.75 * tw:
        lvl = "lower than usual"
    else:
        lvl = "moderate"
    s = ("High uncertainty: band %d vs usual %d EUR/MWh." % (round(w), round(tw))) if (lvl == "high" and tw) else ("High uncertainty." if lvl == "high" else "")
    return s, w, lvl


def _make(rule_name, severity, title, cur, a, b, headline, review, ctx, evidence, idx=0):
    st, en = _window(cur, a, b)
    drivers = _drivers(cur, a, b, ctx)
    unc, w, lvl = _uncertainty(cur, a, b, ctx)
    evidence = dict(evidence, window_mean_width=round(w, 1), uncertainty=lvl)
    msg = " ".join(x for x in (headline, _driver_sentence(drivers), "" if rule_name == "uncertainty" else unc) if x)
    return {"id": "%s-%s-%d" % (rule_name, _iso(st), idx), "rule": rule_name, "severity": severity, "title": title,
            "window": {"start": _iso(st), "end": _iso(en)}, "message": msg, "review": review,
            "evidence": evidence, "drivers": drivers}


def _sev(v, watch, act):
    return "act" if v >= act else "watch"


def _ts64(df):
    return df["delivery_start_utc"].dt.tz_localize(None).to_numpy()


def _overlap(cur, prev):
    """Aligned numpy index arrays (cur_idx, prev_idx) for common delivery times."""
    if prev is None or len(prev) == 0:
        return np.array([], int), np.array([], int)
    _, ci, pi = np.intersect1d(_ts64(cur), _ts64(prev),
                               return_indices=True)
    return ci, pi


# ----------------------------------------------------------------- rules
def _spike(side, cur, prev, acfg, ctx):
    col = "p_spike_" + side
    c = acfg["spike_" + side]
    p = cur[col].to_numpy()
    mrl = acfg.get("min_run_quarters", 2)
    rise = np.zeros(len(cur))
    ci, pi = _overlap(cur, prev)
    if len(ci):
        rise[ci] = p[ci] - prev[col].to_numpy()[pi]
    mask = (p >= c["watch"]) | ((rise >= c["rise"]) & (p >= c["rise_floor"]))
    out = []
    up = side == "high"
    for k, (a, b) in enumerate(find_runs(mask, 1)):
        seg = p[a:b + 1]
        pm = float(seg.max())
        if (b - a + 1) < mrl and pm < c["act"]:
            continue
        rmax = float(rise[a:b + 1].max())
        sev = _sev(pm, c["watch"], c["act"])
        st, en = _window(cur, a, b)
        rose = rmax >= c["rise"]
        what = "exceptionally high" if up else "exceptionally low"
        head = ("%s spike risk peaks at %d%% (%s-%s)%s." % ("High-price" if up else "Low-price", round(pm * 100), _t(st), _t(en),
                                                         (", up %d pp since last update" % round(rmax * 100)) if rose else ""))
        review = "Check position; consider offering flexible capacity (up-regulation)." if up else "Check position; consider cutting production (down-regulation)."
        out.append(_make("spike_" + side, sev, "Price spike risk %s" % ("high" if up else "low"), cur, a, b, head, review, ctx,
                         {"max_p_spike": round(pm, 3), "rise_pp": round(rmax * 100, 1), "threshold_watch": c["watch"]}, k))
    return out


@rule("spike_high")
def spike_high(cur, prev, acfg, ctx):
    return _spike("high", cur, prev, acfg, ctx)


@rule("spike_low")
def spike_low(cur, prev, acfg, ctx):
    return _spike("low", cur, prev, acfg, ctx)


@rule("regulation")
def regulation(cur, prev, acfg, ctx):
    c = acfg["regulation"]
    out = []
    n = c["min_quarters"]
    for dire, col, w, ac in (("up", "p_up", c["up_watch"], c["up_act"]), ("down", "p_down", c["down_watch"], c["down_act"])):
        p = cur[col].to_numpy()
        for k, (a, b) in enumerate(find_runs(p >= w, n)):
            pm = float(p[a:b + 1].mean())
            st, en = _window(cur, a, b)
            sev = _sev(pm, w, ac)
            if dire == "down":
                sev = "info" if pm < ac else "watch"
            head = "%s-regulation likely %s-%s (avg probability %d%%)." % (dire.capitalize(), _t(st), _t(en), round(pm * 100))
            review = "Check whether flexible production or load can be offered." if dire == "up" else "Check whether production can be cut or load raised."
            out.append(_make("regulation_" + dire, sev, "%s-regulation expected" % dire.capitalize(), cur, a, b, head, review, ctx,
                             {"mean_p_" + dire: round(pm, 3), "quarters": b - a + 1}, k))
    return out


@rule("revision")
def revision(cur, prev, acfg, ctx):
    c = acfg["revision"]
    ci, pi = _overlap(cur, prev)
    if not len(ci):
        return []
    out = []
    d = np.zeros(len(cur))
    d[ci] = cur["p50"].to_numpy()[ci] - prev["p50"].to_numpy()[pi]
    for k, (a, b) in enumerate(find_runs(np.abs(d) >= c["p50_shift_watch"], c["min_quarters"])):
        seg = d[a:b + 1]
        j = int(np.argmax(np.abs(seg)))
        mx = float(seg[j])
        st, en = _window(cur, a, b)
        sev = _sev(abs(mx), c["p50_shift_watch"], c["p50_shift_act"])
        head = "P50 for %s-%s moved %s by up to %d EUR/MWh since last update." % (_t(st), _t(en), "up" if mx > 0 else "down", round(abs(mx)))
        out.append(_make("revision", sev, "Forecast revised %s" % ("upward" if mx > 0 else "downward"), cur, a, b, head,
                         "Check what changed (wind, load, flows) and your positions.", ctx,
                         {"max_p50_shift": round(mx, 1)}, k))
    wc = (cur["p90"] - cur["p10"]).to_numpy()[ci]
    wp = (prev["p90"] - prev["p10"]).to_numpy()[pi]
    if wp.mean() > 0 and wc.mean() / wp.mean() >= c["width_ratio"] and wc.mean() - wp.mean() >= c["width_abs"]:
        a, b = 0, len(cur) - 1
        head = "P10-P90 band widened from %d to %d EUR/MWh since last update." % (round(wp.mean()), round(wc.mean()))
        out.append(_make("revision_width", "watch", "Forecast interval widened", cur, a, b, head,
                         "Less certain than an hour ago; treat point forecasts with care.", ctx,
                         {"width_before": round(float(wp.mean()), 1), "width_now": round(float(wc.mean()), 1)}, 0))
    return out


@rule("uncertainty")
def uncertainty(cur, prev, acfg, ctx):
    c = acfg["uncertainty"]
    w = (cur["p90"] - cur["p10"]).to_numpy()
    thr = c["width_watch"]
    if ctx.get("width_q"):
        thr = max(thr, ctx["width_q"]) if c.get("use_hist_floor") else thr
    out = []
    for k, (a, b) in enumerate(find_runs(w >= thr, c["min_quarters"])):
        wm = float(w[a:b + 1].mean())
        st, en = _window(cur, a, b)
        sev = _sev(wm, thr, c["width_act"])
        head = "High uncertainty %s-%s: band averages %d EUR/MWh." % (_t(st), _t(en), round(wm))
        out.append(_make("uncertainty", sev, "High uncertainty", cur, a, b, head,
                         "Avoid over-relying on the point forecast.", ctx,
                         {"mean_width": round(wm, 1), "threshold": thr}, k))
    return out


@rule("conflict")
def conflict(cur, prev, acfg, ctx):
    c = acfg["conflict"]
    p50, spot = cur["p50"].to_numpy(), cur["spot"].to_numpy()
    pu, pd_, sh, sl = (cur[x].to_numpy() for x in ("p_up", "p_down", "p_spike_high", "p_spike_low"))
    ok = ~np.isnan(spot)
    with np.errstate(invalid="ignore"):
        cases = [
            ("up-regulation likely but P50 at/below spot", ok & (pu >= c["dir_prob"]) & (p50 <= spot - c["p50_margin"])),
            ("down-regulation likely but P50 at/above spot", ok & (pd_ >= c["dir_prob"]) & (p50 >= spot + c["p50_margin"])),
            ("high spike likely but direction points down", (sh >= c["spike_p"]) & (pu <= c["low_dir_prob"])),
            ("low spike likely but direction points up", (sl >= c["spike_p"]) & (pu >= 1 - c["low_dir_prob"])),
        ]
    out = []
    for k, (txt, m) in enumerate(cases):
        for j, (a, b) in enumerate(find_runs(m, acfg.get("min_run_quarters", 2))):
            st, en = _window(cur, a, b)
            head = "%s-%s: %s." % (_t(st), _t(en), txt)
            out.append(_make("conflict", "watch", "Conflicting signals", cur, a, b, head,
                             "Worth an expert look.", ctx,
                             {"case": k + 1, "quarters": b - a + 1}, k * 10 + j))
    return out


@rule("expert_review")
def expert_review(cur, prev, acfg, ctx, fired=None):
    """Composite; evaluated after the others (see evaluate())."""
    fired = fired or []
    fam = sorted({a["rule"].split("_")[0] for a in fired})
    c = acfg["expert_review"]
    has_act = any(a["severity"] == "act" for a in fired)
    if len(fam) < c["min_rules"] and not (has_act and len(fam) >= 2):
        return []
    st = min(pd.Timestamp(a["window"]["start"]) for a in fired)
    en = max(pd.Timestamp(a["window"]["end"]) for a in fired)
    a_ = int(np.searchsorted(_ts64(cur), np.datetime64(st.tz_localize(None))))
    b_ = max(a_, int(np.searchsorted(_ts64(cur), np.datetime64(en.tz_localize(None)))) - 1)
    b_ = min(b_, len(cur) - 1)
    names = {"spike": "price-spike risk", "regulation": "regulation need", "revision": "forecast revision",
             "uncertainty": "high uncertainty", "conflict": "conflicting signals"}
    head = "Several signals overlap %s-%s: %s." % (_t(st), _t(en), ", ".join(names.get(f, f) for f in fam))
    sev = "act" if (has_act or len(fam) >= 4) else "watch"
    al = _make("expert_review", sev, "Worth an expert review", cur, a_, b_, head,
               "Review plan, position and flexible capacity together.", ctx,
               {"rules": fam}, 0)
    return [al]


# ----------------------------------------------------------------- entry
def evaluate(cur, prev, acfg, ctx):
    """Run all rules for one origin. Returns alerts sorted by severity (act first), then window start."""
    alerts = []
    for fn in RULES:
        if fn.rule_name == "expert_review":
            continue
        try:
            alerts += fn(cur, prev, acfg, ctx)
        except Exception as e:  # a broken rule must never take the dashboard down
            alerts.append({"id": "error-" + fn.rule_name, "rule": "error", "severity": "info", "title": "Rule error",
                           "window": {"start": _iso(cur["delivery_start_utc"].iloc[0]), "end": _iso(cur["delivery_start_utc"].iloc[-1])},
                           "message": "Rule '%s' failed: %s" % (fn.rule_name, e), "review": "", "evidence": {}, "drivers": []})
    real = [a for a in alerts if a["rule"] != "error"]
    for fn in RULES:
        if fn.rule_name == "expert_review" and real:
            try:
                alerts += fn(cur, prev, acfg, ctx, fired=real)
            except Exception:
                pass
    alerts.sort(key=lambda a: (-SEV[a["severity"]], a["window"]["start"]))
    return alerts


def max_severity(alerts):
    return max((SEV[a["severity"]] for a in alerts if a["rule"] != "error"), default=-1)
