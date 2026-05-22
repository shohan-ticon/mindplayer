"""
Flask example using the tracesnap integration.

Run:
    pip install -e .[flask]
    python examples/flask_app.py

Then hit:
    curl http://127.0.0.1:5050/checkout

Each request writes a trace into ./traces/<id>.json. Open one with:
    tracesnap view traces/<id>.json
"""
import os
import time

from flask import Flask, jsonify
import requests

from tracesnap.integrations.flask import TraceSnap


HERE = os.path.abspath(__file__)
app = Flask(__name__)

# One line: every request is recorded into ./traces/
TraceSnap(app, output_dir="traces", source_files=[HERE])


def validate_cart(items):
    if not items:
        raise ValueError("empty cart")
    cleaned = []
    for x in items:
        cleaned.append(float(x))
    return cleaned


def compute_total(items, coupon_pct):
    subtotal = 0.0
    for amount in items:
        subtotal += amount
    if coupon_pct > 0:
        return subtotal * (1.0 - coupon_pct / 100.0)
    return subtotal


@app.route("/checkout", methods=["GET"])
def checkout():
    raw = [100, 50, 25]
    cart = validate_cart(raw)
    r = requests.get("https://httpbin.org/get", params={"q": "trace-demo"}, timeout=10)
    coupon = 10 if r.status_code == 200 else 0
    total = compute_total(cart, coupon)
    return jsonify({"items": cart, "coupon_pct": coupon, "total": total})


if __name__ == "__main__":
    # threaded=False keeps things simple; settrace is per-thread and one
    # request at a time is plenty for this demo.
    app.run(debug=False, threaded=False, port=5050)
