# mu-smarterliving

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Smart meter API client and Home Assistant integration for Manx Utilities (Isle of Man) electricity monitoring — a zero-dependency Python CLI for the Hildebrand / Glowmarkt platform behind SmarterLiving.** Query half-hourly consumption (kWh) and cost data from your IoM smart meter, run a lightweight local JSON bridge, and surface live energy sensors in [Home Assistant](https://www.home-assistant.io/) — no custom components, no MQTT, no pip installs.

Stdlib only — one file. Works with any utility running Hildebrand's Glowmarkt white-label platform.

Zero-dependency Python CLI + HTTP bridge for the **Manx Utilities SmarterLiving** smart-meter platform (Hildebrand/Glowmarkt backend). Query your electricity consumption and cost, or run a tiny JSON server that Home Assistant can poll natively.


## The API (reverse-engineered)

- Base URL: `https://api.manxutilities.im/api/v0-1/`
- Auth: `POST auth` with `{"username": "...", "password": "..."}` → JSON containing a JWT (`token`), valid ~7 days. Send it back as the `token` header.
- Every request needs an `applicationId` header (the constant below is what the official SmarterLiving web app sends — it is not a secret).
- Data endpoints are standard Glowmarkt: `virtualentity`, `resource/{id}/readings?from=ISO&to=ISO&period=P1D|PT30M&function=sum|max`, `resource/{id}/current`.

## CLI quickstart

```sh
export MU_USER=you@example.com
export MU_PASS='yourpassword'

mu.py login                      # authenticate, cache token to ~/.cache/mu-smarterliving/
mu.py ve                         # list virtual entities (meters) — grab a resourceId
mu.py readings <resourceId> --from 2026-09-01T00:00:00 --to 2026-09-08T00:00:00 --period P1D --function sum
mu.py current <resourceId>       # latest half-hourly reading
mu.py raw virtualentity          # authenticated pass-through to any upstream GET path
```

## Home Assistant bridge

```sh
mu.py serve --port 8087          # stdlib http.server; token handled internally
```

Endpoints:
- `GET /health` — liveness
- `GET /summary` — per-resource snapshot: last half-hourly value, today, yesterday, last-7-day and month-to-date sums
- `GET /current/<resourceId>` — upstream `/current`
- `GET /readings/<resourceId>?from=&to=&period=&function=` — upstream time series

Example systemd user unit:

```ini
[Unit]
Description=Manx Utilities SmarterLiving bridge

[Service]
ExecStart=/usr/bin/env python3 %h/mu-smarterliving/mu.py serve --port 8087
EnvironmentFile=%h/mu-smarterliving/.env
Restart=on-failure

[Install]
WantedBy=default.target
```

Example HA `configuration.yaml` (polls every 5 min; upstream data is half-hourly):

```yaml
rest:
  - resource: http://<bridge-host>:8087/summary
    scan_interval: 300
    sensor:
      - name: "Electricity Usage"
        unique_id: mu_electricity_usage
        value_template: >-
          {{ (value_json.resources | selectattr("classifier","equalto","electricity.consumption")
             | map(attribute="value") | first) }}
        unit_of_measurement: "kWh"
        device_class: energy
        state_class: measurement
      - name: "Electricity Today"
        unique_id: mu_electricity_today
        value_template: >-
          {{ (value_json.resources | selectattr("classifier","equalto","electricity.consumption")
             | map(attribute="today") | first) }}
        unit_of_measurement: "kWh"
        device_class: energy
        state_class: total_increasing
```

The same shape works for cost resources (`electricity.consumption.cost`, pence) and for `yesterday`/`last7_sum`/`mtd_sum` attributes.

## Configuration

| Env var | Purpose |
|---|---|
| `MU_USER` / `MU_PASS` | SmarterLiving portal credentials |
| `MU_BASE_URL` | Override API base (default Manx Utilities instance) |
| `MU_PORT` | Default port for `serve` |

Token cache path: `~/.cache/mu-smarterliving/token.json` (0600).

## Notes

- Works for any utility running Hildebrand's Glowmarkt white-label with their own domain; point `MU_BASE_URL` at it. (The original `api.glowmarkt.com` app IDs differ per application.)
- Prepay/top-up endpoints exist in the platform but are not implemented here.
- Not affiliated with Manx Utilities or Hildebrand Technology Ltd.

## License

MIT
