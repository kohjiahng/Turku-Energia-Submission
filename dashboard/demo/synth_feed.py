"""Simulated live feed for the Live tab (synthetic data and a toy forecaster, see fake_world.py).

One simulated hour passes every `tick` real seconds: a new 40-quarter forecast is appended to the forecast CSV and the quarters
that have been delivered get their realised prices. Files in demo/out/<session> are replaced atomically; the session's Live app re-reads them.
Every browser page load is its own session (see LiveSessions): its own simulated clock, restarted at the current hour on reload.
"""
import os, re, shutil, sys, threading, time
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, HERE)
import fake_world as fw  # noqa: E402

Q = fw.Q
LABEL = "Live (simulated data)"


def _atomic(path, fn):
    tmp = path + ".tmp"
    fn(tmp)
    os.replace(tmp, path)


_WORLD, _WORLD_LOCK = {}, threading.Lock()


def _world(start, history_days, seed):
    """The simulated world and the forecasts up to now are the same for every session started in the same hour: build them once."""
    key = (start, history_days, seed)
    with _WORLD_LOCK:
        if key not in _WORLD:
            _WORLD.clear()
            w = fw.build_world(start=start, days=14, seed=seed)
            first, now = w.index[0] + pd.Timedelta(hours=6), (w.index[0] + pd.Timedelta(days=history_days)).floor("h")
            _WORLD[key] = (w, [fw.forecast_rows(w, o, seed) for o in pd.date_range(first, now, freq="h")])
        return _WORLD[key]


class SynthFeed:
    def __init__(self, base_cfg, tick=6.0, history_days=5.0, seed=7, out=OUT):
        self.tick, self.seed, self.base, self.speed, self.out = tick, seed, base_cfg, 1.0, out   # speed > 1 fast-forwards the feed
        os.makedirs(out, exist_ok=True)
        # the simulated clock starts at the current hour: `history_days` of history before it, then one simulated hour per tick
        start = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(days=history_days)
        self.w, frames = _world(start.strftime("%Y-%m-%dT%H:%M:%SZ"), history_days, seed)   # shared by sessions started in the same hour
        self.now = (self.w.index[0] + pd.Timedelta(days=history_days)).floor("h")
        self.last = self.w.index[-1] - pd.Timedelta(hours=11)
        self.frames = list(frames)
        self.stop = threading.Event()
        self.write()

    def cfg(self):
        cfg = dict(self.base)
        cfg["_base"] = self.out
        cfg["runs"] = {"live": {"label": LABEL, "forecast_csv": "live_forecast.csv"}}
        cfg["actuals"] = dict(self.base["actuals"], inputs_parquet="live_inputs.parquet", spot_csv="live_spot.csv", actuals_csv=None)
        cfg["glossary_path"] = os.path.abspath(os.path.join(HERE, "..", self.base.get("glossary_path", "drivers_glossary.json")))
        cfg.pop("trust", None)
        return cfg

    def write(self):
        w, now = self.w, self.now
        ser = self.base["actuals"]["series"]
        fc = pd.concat(self.frames, ignore_index=True)
        for c in ("forecast_origin_utc", "delivery_start_utc", "data_cutoff_utc"):
            fc[c] = fw.to_iso(fc[c])
        _atomic(os.path.join(self.out, "live_forecast.csv"), lambda p: fc.to_csv(p, index=False))
        done, ahead = w.loc[: now - Q], w.loc[: now + pd.Timedelta(hours=10)]
        parts = []
        for key, frame in (("imbalance", done), ("direction", done), ("wind_fc", ahead), ("cons_fc", ahead), ("solar_fc", ahead)):
            parts.append(pd.DataFrame({"series_id": ser[key], "timestamp_utc": frame.index, "value": frame[key].to_numpy()}))
        _atomic(os.path.join(self.out, "live_inputs.parquet"), lambda p: pd.concat(parts, ignore_index=True).to_parquet(p, index=False))
        sp = pd.DataFrame({"series_id": ser["spot"], "timestamp_utc": fw.to_iso(pd.Series(ahead.index)), "value": ahead["spot"].to_numpy()})
        _atomic(os.path.join(self.out, "live_spot.csv"), lambda p: sp.to_csv(p, index=False))

    def start(self):
        def run():
            while self.now < self.last and not self.stop.wait(self.tick / self.speed):
                self.now = self.now + pd.Timedelta(hours=1)
                self.frames.append(fw.forecast_rows(self.w, self.now, self.seed))
                self.write()
            if not self.stop.is_set():
                print("live feed %s: end of the simulated world reached; the feed stops here" % os.path.basename(self.out), flush=True)
        threading.Thread(target=run, daemon=True).start()

    def close(self):
        self.stop.set()
        shutil.rmtree(self.out, ignore_errors=True)


SID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class LiveSessions:
    """One simulated feed (own clock, world, files and server-side App) per browser page load, keyed by the page's `sid`.

    A session is created by its first request, restarts at the current hour, and is dropped after `ttl` idle seconds
    (the page polls every few seconds), so a reload always starts a fresh clock.
    """

    def __init__(self, cfg, tick, history_days, make_app, ttl=300.0, max_sessions=24):
        self.cfg, self.tick, self.hist, self.make_app, self.ttl, self.max = cfg, tick, history_days, make_app, ttl, max_sessions
        self.items, self.lock = {}, threading.Lock()
        threading.Thread(target=self._reap, daemon=True).start()

    def get(self, sid, claim=True):
        """The session for `sid`, built on first use. claim=False builds it without starting its clock (pre-warming); the
        clock starts at the first claim, so a session warmed a minute before it is used still starts at the current hour of use."""
        if not SID.match(sid or ""):
            return None
        with self.lock:
            it = self.items.get(sid)
            builder = it is None
            if builder:
                if len(self.items) >= self.max:   # drop the least recently used session
                    self._drop(min((k for k, v in self.items.items() if v["ready"].is_set()), key=lambda k: self.items[k]["seen"], default=None))
                it = {"ready": threading.Event(), "seen": time.time(), "started": False}
                self.items[sid] = it
            it["seen"] = time.time()
        if builder:   # heavy work outside the lock so other sessions are not blocked
            try:
                feed = SynthFeed(self.cfg, self.tick, self.hist, out=os.path.join(OUT, sid))
                it["feed"] = feed
                it["app"], it["handler"] = self.make_app(feed.cfg())
            except Exception:
                with self.lock:
                    self.items.pop(sid, None)
                it["ready"].set()
                raise
            it["ready"].set()
            print("live session %s built (%d active)" % (sid[:8], len(self.items)), flush=True)
        else:
            it["ready"].wait(60)
            if "app" not in it:
                return None
        if claim:
            with self.lock:
                if not it["started"]:
                    it["started"] = True
                    it["feed"].start()
        return it

    def _drop(self, sid):
        if sid is None:
            return
        it = self.items.pop(sid, None)
        if it:
            it["feed"].close()
            print("live session %s closed (%d active)" % (sid[:8], len(self.items)), flush=True)

    def _reap(self):
        while True:
            time.sleep(15)
            with self.lock:
                for k in [k for k, v in self.items.items() if v["ready"].is_set() and time.time() - v["seen"] > self.ttl]:
                    self._drop(k)
