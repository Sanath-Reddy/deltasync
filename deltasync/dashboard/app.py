"""Small Flask dashboard that reads the server's Prometheus metrics.

    python -m deltasync.dashboard.app --metrics http://127.0.0.1:9100/metrics
"""

import argparse
import urllib.request

from flask import Flask, jsonify, render_template_string
from prometheus_client.parser import text_string_to_metric_families

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="2">
<title>DeltaSync</title>
<style>
body{font:15px system-ui,sans-serif;margin:2rem auto;max-width:44rem;padding:0 1rem;color:#222}
table{border-collapse:collapse;width:100%}td{padding:.4rem .6rem;border-bottom:1px solid #ddd}
td:last-child{text-align:right;font-variant-numeric:tabular-nums}h1{font-size:1.3rem}
.saved{font-size:1.1rem;margin:1rem 0}
</style></head><body>
<h1>DeltaSync server</h1>
{% if error %}<p>Metrics unavailable: {{ error }}</p>{% else %}
<p class="saved">Bytes reused instead of sent: <b>{{ "%.1f"|format(saved) }}%</b></p>
<table>{% for k, v in rows %}<tr><td>{{ k }}</td><td>{{ v }}</td></tr>{% endfor %}</table>
{% endif %}</body></html>"""


def read_metrics(text):
    """Flatten Prometheus text into {name{labels}: value}."""
    out = {}
    for fam in text_string_to_metric_families(text):
        for s in fam.samples:
            if s.name.endswith("_created") or s.name.endswith("_bucket"):
                continue
            label = ",".join(f"{k}={v}" for k, v in sorted(s.labels.items()))
            out[s.name + (f"{{{label}}}" if label else "")] = s.value
    return out


def summary(m):
    lit = m.get("deltasync_literal_bytes_total", 0)
    matched = m.get("deltasync_matched_bytes_total", 0)
    saved = 100 * matched / (lit + matched) if lit + matched else 0.0
    return saved


def create_app(metrics_url, fetch=None):
    fetch = fetch or (lambda: urllib.request.urlopen(metrics_url, timeout=2).read().decode())
    app = Flask(__name__)

    def load():
        try:
            return read_metrics(fetch()), None
        except OSError as e:
            return {}, str(e)

    @app.get("/")
    def index():
        m, error = load()
        rows = [(k.removeprefix("deltasync_"), f"{v:,.0f}" if v == int(v) else f"{v:,.3f}") for k, v in sorted(m.items())]
        return render_template_string(PAGE, rows=rows, saved=summary(m), error=error)

    @app.get("/api/metrics")
    def api():
        m, error = load()
        return jsonify({"metrics": m, "saved_pct": summary(m), "error": error})

    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metrics", default="http://127.0.0.1:9100/metrics")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    create_app(args.metrics).run(port=args.port)


if __name__ == "__main__":
    main()
