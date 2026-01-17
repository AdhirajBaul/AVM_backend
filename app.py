import os
import uuid
import json
import hmac
import hashlib
import sqlite3

from flask import Flask, request, jsonify, abort, render_template
from dotenv import load_dotenv
import razorpay

from db import init_db

# --------------------------------------------------
# LOAD ENV
# --------------------------------------------------

load_dotenv()

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")

ESP32_BEARER_TOKEN = os.getenv("ESP32_BEARER_TOKEN")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev_secret")

if not all([
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    RAZORPAY_WEBHOOK_SECRET,
    ESP32_BEARER_TOKEN
]):
    raise RuntimeError("Missing required environment variables")

# --------------------------------------------------
# APP INIT
# --------------------------------------------------

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

razorpay_client = razorpay.Client(
    auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
)

init_db()

# --------------------------------------------------
# AUTH HELPER (ESP32)
# --------------------------------------------------

def require_esp32_auth():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        abort(401)
    token = auth.split(" ", 1)[1]
    if token != ESP32_BEARER_TOKEN:
        abort(403)

# --------------------------------------------------
# WEBSITE ROUTES
# --------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/orders")
def orders():
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute("SELECT * FROM orders ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()

    formatted = []
    for r in rows:
        formatted.append({
            "order_id": r[1],
            "amount": r[2],
            "paid": bool(r[4]),
            "payment_id": r[5],
            "created_at": r[6]
        })

    return render_template("orders.html", orders=formatted)

# --------------------------------------------------
# API: CREATE PAYMENT LINK (ESP32)
# --------------------------------------------------

@app.route("/api/create_payment_uri", methods=["POST"])
def create_payment_uri():
    require_esp32_auth()

    data = request.get_json(silent=True) or {}
    amount = int(data.get("amount", 0))

    if amount <= 0:
        return jsonify({"error": "Invalid amount"}), 400

    # 1️⃣ Create Razorpay Order
    order = razorpay_client.order.create({
        "amount": amount * 100,
        "currency": "INR",
        "receipt": f"rcpt_{uuid.uuid4().hex[:8]}"
    })

    # 2️⃣ Create Payment Link
    link = razorpay_client.payment_link.create({
        "amount": amount * 100,
        "currency": "INR",
        "reference_id": order["id"],
        "description": "Automated Vending Machine Payment",
        "notes": {
            "order_id": order["id"]
        }
    })

    # 3️⃣ Store order in DB
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO orders (order_id, amount, payment_uri)
        VALUES (?, ?, ?)
    """, (order["id"], amount, link["short_url"]))
    conn.commit()
    conn.close()

    return jsonify({
        "order_id": order["id"],
        "payment_uri": link["short_url"]
    })

# --------------------------------------------------
# API: PAYMENT STATUS (ESP32 FALLBACK)
# --------------------------------------------------

@app.route("/api/payment_status/<order_id>", methods=["GET"])
def payment_status(order_id):
    require_esp32_auth()

    payments = razorpay_client.payment.all({
        "order_id": order_id
    })

    for p in payments.get("items", []):
        if p["status"] == "captured":
            conn = sqlite3.connect("orders.db")
            c = conn.cursor()
            c.execute("""
                UPDATE orders
                SET paid=1, payment_id=?
                WHERE order_id=?
            """, (p["id"], order_id))
            conn.commit()
            conn.close()

            return jsonify({
                "paid": True,
                "payment_id": p["id"],
                "method": p["method"]
            })

    return jsonify({"paid": False})

# --------------------------------------------------
# WEBHOOK: RAZORPAY (PRIMARY VERIFICATION)
# --------------------------------------------------

@app.route("/webhook/razorpay", methods=["POST"])
def razorpay_webhook():
    payload = request.data
    received_sig = request.headers.get("X-Razorpay-Signature")

    expected_sig = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(received_sig, expected_sig):
        abort(400)

    event = json.loads(payload)

    if event["event"] == "payment.captured":
        payment = event["payload"]["payment"]["entity"]
        order_id = payment.get("order_id")
        payment_id = payment.get("id")

        if order_id:
            conn = sqlite3.connect("orders.db")
            c = conn.cursor()
            c.execute("""
                UPDATE orders
                SET paid=1, payment_id=?
                WHERE order_id=?
            """, (payment_id, order_id))
            conn.commit()
            conn.close()

    return jsonify({"status": "ok"})

@app.route("/contact")
def contact():
    return render_template("contact.html")

@app.route("/shipping-policy")
def shipping_policy():
    return render_template("shipping.html")

@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/refunds")
def refunds():
    return render_template("refunds.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

# --------------------------------------------------
# MAIN
# --------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

