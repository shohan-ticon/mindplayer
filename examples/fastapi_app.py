"""
FastAPI example using the tracesnap middleware.

Run:
    pip install -e .[fastapi]
    uvicorn examples.fastapi_app:app --port 5051

Hit:
    curl http://127.0.0.1:5051/checkout

Traces land in ./traces/<id>.json.
"""
import os

import requests
from fastapi import FastAPI

from tracesnap.integrations.fastapi import install


HERE = os.path.abspath(__file__)
app = FastAPI()
install(app, output_dir="traces", source_files=[HERE])


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


@app.get("/checkout")
def checkout():
    raw = [100, 50, 25]
    cart = validate_cart(raw)
    r = requests.get("https://httpbin.org/get", params={"q": "trace-demo"}, timeout=10)
    coupon = 10 if r.status_code == 200 else 0
    total = compute_total(cart, coupon)
    return {"items": cart, "coupon_pct": coupon, "total": total}
