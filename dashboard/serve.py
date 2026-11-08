#!/usr/bin/env python3
"""Radar dashboard server (stdlib HTTP + pandas).

    .venv/bin/python -W ignore final_submission/dashboard/serve.py [--port 8770]

Open http://127.0.0.1:8770/v/5_radar/ (the root redirects there). Explore tab: the forecast runs from config.json.
Live tab: a simulated feed under /live/. API: /api/meta, timeline, forecast, alerts (server.py), track, metrics, driver_shares.
"""
import argparse, mimetypes, os, sys, threading
from base64 import b64encode
from hmac import compare_digest
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import adapters, server, track_api, metrics_api  # noqa: E402

VARIANTS = os.path.join(HERE, "variants")


def build_handler(base, app, live=None, password=None):
    """live = LiveSessions: every page load has its own simulated Live app under /live/ (e.g. /live/api/forecast?sid=<id>)."""
    credentials = b64encode(f"radar:{password}".encode()) if password else None

    class H(base):
        def do_GET(self):
            u = urlparse(self.path)
            p = u.path
            if p == "/healthz":
                return self._send(200, b"OK", "text/plain")
            if p == "/robots.txt":   # keep search engines away from the (unlisted) site
                return self._send(200, b"User-agent: *\nDisallow: /\n", "text/plain")
            if credentials is not None:
                scheme, _, token = self.headers.get("Authorization", "").partition(" ")
                if scheme.lower() != "basic" or not compare_digest(token.encode(), credentials):
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="Radar"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            use_app, use_base, feed = app, base, None
            if live and p.startswith("/live/"):
                self.path = self.path[5:]
                u = urlparse(self.path)
                p = u.path
                if p == "/api/ping":   # lets the page know the Live tab exists, without starting a session
                    return self._json(200, {"live": True})
                sid = (parse_qs(u.query).get("sid") or [""])[0]
                if p == "/api/warm":   # build a session ahead of time (its clock starts when the page first uses it)
                    return self._json(200, {"ready": live.get(sid, claim=False) is not None})
                sess = live.get(sid)
                if sess is None:
                    return self._json(400, {"error": "missing or invalid session id (sid)", "status": 400})
                use_app, use_base, feed = sess["app"], sess["handler"], sess["feed"]
            if p == "/__wait.js":   # screenshot helper: hold the load event for ?ms=
                import time
                time.sleep(min(15000, int((parse_qs(u.query).get("ms") or ["3000"])[0])) / 1000)
                return self._send(200, b"", "text/javascript")
            if p == "/api/speed" and feed is not None:   # /live/api/speed?x=4&sid=..: fast-forward this session's simulated feed
                try:
                    x = float((parse_qs(u.query).get("x") or ["1"])[0])
                except ValueError:
                    x = 1.0
                feed.speed = min(16.0, max(0.25, x))
                return self._json(200, {"speed": feed.speed, "seconds_per_simulated_hour": round(feed.tick / feed.speed, 2)})
            if p == "/api/driver_shares":
                try:
                    return self._json(200, metrics_api.route_shares(use_app)(parse_qs(u.query)))
                except server.ApiError as e:
                    return self._json(e.status, {"error": e.msg, "status": e.status})
                except Exception as e:
                    import traceback; traceback.print_exc()
                    return self._json(500, {"error": "internal error: %s" % e, "status": 500})
            if p == "/api/metrics":
                try:
                    return self._json(200, metrics_api.route(use_app)(parse_qs(u.query)))
                except server.ApiError as e:
                    return self._json(e.status, {"error": e.msg, "status": e.status})
                except Exception as e:
                    import traceback; traceback.print_exc()
                    return self._json(500, {"error": "internal error: %s" % e, "status": 500})
            if p == "/api/track":
                try:
                    return self._json(200, track_api.route(use_app)(parse_qs(u.query)))
                except server.ApiError as e:
                    return self._json(e.status, {"error": e.msg, "status": e.status})
                except Exception as e:
                    import traceback; traceback.print_exc()
                    return self._json(500, {"error": "internal error: %s" % e, "status": 500})
            if p in ("/", "/index.html"):
                self.send_response(302)
                self.send_header("Location", "/v/5_radar/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if p.startswith("/v/"):
                rel = p[3:]
                if rel.endswith("/") or "." not in os.path.basename(rel):
                    rel = rel.rstrip("/") + "/index.html"
                fp = os.path.normpath(os.path.join(VARIANTS, rel))
                if fp.startswith(VARIANTS + os.sep) and os.path.isfile(fp):
                    return self._file(fp)
                return self._json(404, {"error": "not found: %s" % p, "status": 404})
            if p.startswith("/shared/"):
                fp = os.path.normpath(os.path.join(HERE, p.lstrip("/")))
                if fp.startswith(os.path.join(HERE, "shared") + os.sep) and os.path.isfile(fp):
                    return self._file(fp)
            return use_base.do_GET(self)

        def _file(self, fp):
            ct = mimetypes.guess_type(fp)[0] or "application/octet-stream"
            if ct in ("text/javascript", "application/javascript"):
                ct = "text/javascript"
            st = os.stat(fp)
            etag = '"%x-%x"' % (st.st_mtime_ns, st.st_size)
            if self.headers.get("If-None-Match") == etag:   # unchanged: the browser reuses its copy (no re-download on tab switches)
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            with open(fp, "rb") as fh:
                self._send(200, fh.read(), ct + ("; charset=utf-8" if ct.startswith("text/") else ""), {"ETag": etag})
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--live-tick", type=float, default=6.0, help="real seconds per simulated hour of the Live tab (0 = no Live tab)")
    ap.add_argument("--live-history-days", type=float, default=5.0)
    a = ap.parse_args()
    password = os.environ.get("RADAR_PASSWORD")
    if a.host != "127.0.0.1" and not password:   # no login: anyone who has the URL can open the site (the organisers permit this use)
        print("WARNING: serving on %s without a password (RADAR_PASSWORD is not set); anyone with the URL can open the site" % a.host, flush=True)
    cfg = adapters.load_config(a.config)
    if data_dir := os.environ.get("RADAR_DATA_DIR"):
        data_dir = os.path.abspath(data_dir)
        if url := os.environ.get("RADAR_DATA_URL"):   # hosts without SSH: pull the data bundle (zip) from a private https link
            import fetch_data
            fetch_data.fetch(url, data_dir, refresh=os.environ.get("RADAR_DATA_REFRESH") == "1", log=lambda m: print(m, flush=True))
        for key in ("inputs_parquet", "spot_csv", "actuals_csv"):
            if cfg["actuals"].get(key):
                cfg["actuals"][key] = os.path.join(data_dir, os.path.basename(cfg["actuals"][key]))
        for run in cfg["runs"].values():
            run["forecast_csv"] = os.path.join(data_dir, os.path.basename(run["forecast_csv"]))
        cfg["metrics_data_dir"] = data_dir
    app = server.App(cfg)
    app.reload()
    live = None
    if a.live_tick > 0:   # Live tab: simulated data, one independent feed per browser page load, under /live/
        sys.path.insert(0, os.path.join(HERE, "demo"))
        import synth_feed

        def make_app(c):
            la = server.App(c)
            la.reload()
            for r in la.runs.values():
                if r.df is not None:
                    threading.Thread(target=la.timeline, args=(r,), daemon=True).start()
            return la, server.make_handler(la)

        live = synth_feed.LiveSessions(cfg, a.live_tick, a.live_history_days, make_app)
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer((a.host, a.port), build_handler(server.make_handler(app), app, live, password))
    srv.daemon_threads = True
    for r in app.runs.values():
        if r.df is not None:
            threading.Thread(target=app.timeline, args=(r,), daemon=True).start()
    print(f"Radar dashboard listening on {a.host}:{a.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
