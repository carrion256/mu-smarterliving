#!/usr/bin/env python3
"""mu_smarterliving.py — Manx Utilities SmarterLiving API wrapper.

Hildebrand/Glowmarkt backend: https://api.manxutilities.im/api/v0-1/
Every call needs: applicationId header (constant) + token header (JWT from POST auth).

Usage:
  mu.py login                          # authenticate, cache token (~/.cache/mu/token.json)
  mu.py ve                             # list virtual entities (meters)
  mu.py resources                      # all resources across VEs
  mu.py readings <resourceId> [--from ISO] [--to ISO] [--period PT30M|P1D|P1W|P1M] [--function sum|max]
  mu.py current <resourceId>           # latest reading
  mu.py raw <upstream_path>            # authenticated pass-through, e.g. 'tariff/...'
Env:
  MU_USER / MU_PASS   SmarterLiving portal credentials (required; no defaults)
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("MU_BASE_URL", "https://api.manxutilities.im/api/v0-1/")
APPLICATION_ID = "8f56d0c3-351b-43aa-bf86-b49dbacd18dc"
CACHE_PATH = os.path.expanduser("~/.cache/mu-smarterliving/token.json")

USERNAME = os.environ.get("MU_USER", "")
PASSWORD = os.environ.get("MU_PASS", "")


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def _request(method, url, headers=None, body=None):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            raise ApiError(e.code, raw[:500])
    except urllib.error.URLError as e:
        raise ConnectionError(f"network error: {e.reason}")


def _load_cached_token():
    try:
        with open(CACHE_PATH) as f:
            d = json.load(f)
        if time.time() < d.get("exp", 0) - 300:   # 5-min skew margin
            return d["token"]
    except (OSError, KeyError, json.JSONDecodeError):
        pass
    return None


def _save_token(token, exp):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump({"token": token, "exp": exp}, f)
    os.chmod(CACHE_PATH, 0o600)


def _login():
    if not USERNAME or not PASSWORD:
        sys.exit("set MU_USER and MU_PASS env vars")
    # Auth endpoint wants no trailing slash normalization issues; base already has one.
    url = urllib.parse.urljoin(BASE_URL, "auth")
    st, data = _request("POST", url,
                        headers={"Content-Type": "application/json",
                                 "applicationId": APPLICATION_ID},
                        body={"username": USERNAME, "password": PASSWORD})
    if st == 401 or not data.get("valid"):
        raise ApiError(st, "auth failed: invalid credentials")
    _save_token(data["token"], data["exp"])
    return data


def get_token(force=False):
    if force:
        return _login()
    tok = _load_cached_token()
    if tok:
        return tok
    d = _login()
    return d["token"]


def authed_get(path, params=None, retry_on_401=True):
    url = urllib.parse.urljoin(BASE_URL, path.lstrip("/"))
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    headers = {"Content-Type": "application/json",
               "applicationId": APPLICATION_ID,
               "token": get_token()}
    st, data = _request("GET", url, headers=headers)
    if st == 401 and retry_on_401:
        headers["token"] = get_token(force=True)
        st, data = _request("GET", url, headers=headers)
    if st >= 400:
        raise ApiError(st, str(data)[:500])
    return data


# ---- high-level ----

def list_ve():
    return authed_get("virtualentity")


def all_resources():
    out = []
    for ve in list_ve():
        for r in ve.get("resources", []):   # resources are embedded in /ve response
            if isinstance(r, str):
                r = {"resourceId": r}
            r["_veId"] = ve.get("veId", ve.get("virtualEntityId"))
            out.append(r)
    return out


def readings(resource_id, from_ts, to_ts, period="PT30M", function="sum"):
    return authed_get(f"resource/{resource_id}/readings",
                      {"from": from_ts, "to": to_ts,
                       "period": period, "offset": "-60", "function": function})


def current(resource_id):
    return authed_get(f"resource/{resource_id}/current")


def _readings_bucket(rid, frm, to, period="P1D", function="sum"):
    d = readings(rid, frm, to, period, function)
    return d.get("data") or []


def _day_totals(rid):
    """today (accumulating), yesterday, last7, mtd sums.

    ponytail: day buckets are UTC because the upstream API returns UTC day
    boundaries; IoM local-day edges would need an offset-shifted second query.
    """
    import datetime as dt
    now = time.gmtime()
    today_mid = dt.datetime(now.tm_year, now.tm_mon, now.tm_mday, tzinfo=dt.timezone.utc)
    yest_mid = today_mid - dt.timedelta(days=1)
    month_start = today_mid.replace(day=1)
    t_end = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 3600))
    f = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S")
    pts_day = _readings_bucket(rid, f(today_mid - dt.timedelta(days=8)), t_end, "P1D", "sum")
    by_day = {p[0]: p[1] for p in pts_day if len(p) > 1}
    def near(target_ts):
        ts = int(target_ts.timestamp())
        ks = sorted(by_day)
        best = min(ks, key=lambda k: abs(k - ts)) if ks else None
        return by_day.get(best) if best is not None and abs(best - ts) < 86400 else None
    mtd_pts = [v for k, v in sorted(by_day.items()) if k >= int(month_start.timestamp())]
    w7 = [v for k, v in sorted(by_day.items()) if k >= int((today_mid - dt.timedelta(days=6)).timestamp())]
    return {"today": near(today_mid),
            "yesterday": near(yest_mid),
            "last7_sum": round(sum(w7), 3) if w7 else None,
            "mtd_sum": round(sum(mtd_pts[:-1]) if mtd_pts and today_mid.timestamp() not in by_day else sum(mtd_pts), 3) if mtd_pts else None}


def summary():
    """One-call snapshot for HA polling: live value + day/period totals per resource."""
    out = []
    for r in all_resources():
        rid = r["resourceId"]
        try:
            cur = current(rid)
        except (ApiError, ConnectionError):
            continue
        data = cur.get("data") or []
        # /current sometimes returns empty; fall back to last half-hourly slot
        if not data:
            to_s = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            from_s = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 7200))
            d = readings(rid, from_s, to_s, "PT30M", "max")
            data = d.get("data") or []
        last = data[-1] if data else [None, None]
        try:
            totals = _day_totals(rid)
        except (ApiError, ConnectionError):
            totals = {}
        out.append({"resourceId": rid,
                    "_veId": r.get("_veId"),
                    "units": cur.get("units"),
                    "classifier": cur.get("classifier"),
                    "timestamp": last[0],
                    "value": last[1] if len(last) > 1 else None,
                    **totals})
    return {"ok": True, "updated": int(time.time()), "resources": out}


def main():
    p = argparse.ArgumentParser(description="Manx Utilities SmarterLiving API CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="authenticate & cache token")
    sub.add_parser("ve", help="list virtual entities")
    sub.add_parser("resources", help="list all resources across VEs")

    r = sub.add_parser("readings", help="time-series readings")
    r.add_argument("resource_id")
    r.add_argument("--from", dest="from_ts", required=True, help="ISO, e.g. 2026-09-01T00:00:00")
    r.add_argument("--to", dest="to_ts", required=True)
    r.add_argument("--period", default="P1D", choices=["PT30M", "P1H", "P1D", "P1W", "P1M"])
    r.add_argument("--function", default="sum", choices=["sum", "max", "min", "avg"])

    c = sub.add_parser("current", help="latest reading")
    c.add_argument("resource_id")

    rw = sub.add_parser("raw", help="authenticated pass-through GET to upstream path")
    rw.add_argument("path")

    sv = sub.add_parser("serve", help="run HTTP JSON API for Home Assistant")
    sv.add_argument("--port", type=int, default=int(os.environ.get("MU_PORT", "8087")))
    sv.add_argument("--bind", default="0.0.0.0")

    args = p.parse_args()
    try:
        if args.cmd == "login":
            print(json.dumps(_login(), indent=2))
        elif args.cmd == "ve":
            print(json.dumps(list_ve(), indent=2))
        elif args.cmd == "resources":
            print(json.dumps(all_resources(), indent=2))
        elif args.cmd == "readings":
            print(json.dumps(readings(args.resource_id, args.from_ts, args.to_ts,
                                      args.period, args.function), indent=2))
        elif args.cmd == "current":
            print(json.dumps(current(args.resource_id), indent=2))
        elif args.cmd == "raw":
            print(json.dumps(authed_get(args.path), indent=2))
        elif args.cmd == "serve":
            serve(args.port, args.bind)
    except (ApiError, ConnectionError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


# ---------- serve mode (stdlib http.server, no deps) ----------

def serve(port, bind):
    import http.server
    import socketserver

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path.strip("/")
            try:
                if path == "health":
                    return self._send(200, {"ok": True})
                if path == "summary":
                    return self._send(200, summary())
                if path.startswith("current/"):
                    return self._send(200, current(path.split("/", 1)[1]))
                if path.startswith("readings/"):
                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    rid = path.split("/", 1)[1]
                    d = readings(rid,
                                 q.get("from", [""])[0], q.get("to", [""])[0],
                                 q.get("period", ["PT30M"])[0], q.get("function", ["sum"])[0])
                    return self._send(200, d)
                return self._send(404, {"error": "unknown path"})
            except (ApiError, ConnectionError) as e:
                # map upstream auth failure to 502 so HA shows unavailable, not a crash
                return self._send(502, {"error": str(e)})

        def log_message(self, fmt, *a):
            pass  # keep tokens/paths out of any persistent log

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((bind, port), Handler) as httpd:
        print(f"serving on {bind}:{port}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
