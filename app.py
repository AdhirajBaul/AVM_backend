import os
import json
import hmac
import hashlib
import uuid
import sqlite3

from flask import Flask, request, jsonify, abort, render_template
from dotenv import load_dotenv
import razorpay

from db import init_db, insert_order, mark_paid, mark_failed, get_order, list_orders

# --------------------------------------------------
# LOAD ENV
# --------------------------------------------------
load_dotenv()

RAZORPAY_KEY_ID = os.getenv("zp_live_S5PU9JqyrwqHhb")
RAZORPAY_KEY_SECRET = os.getenv("tisGx5OJttEEM8cz0hChNjlw")
RAZORPAY_WEBHOOK_SECRET = os.getenv("tisGx5OJttEEM8cz0hChNjlw")

ESP32_BEARER_TOKEN = os.getenv("avm_esp32_9fK2pQ8xR7A_L0ngSeCrEt")
FLASK_SECRET_KEY = os.getenv("some_long_random_secret", "dev_secret")

if not all([RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET, ESP32_BEARER_TOKEN]):
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
    if auth.split(" ", 1)[1] != ESP32_BEARER_TOKEN:
        abort(403)

# --------------------------------------------------
# WEBSITE ROUTES
# --------------------------------------------------
@app.route("/")
def home():
    return render_template("index.html", razorpay_key_id=RAZORPAY_KEY_ID)

@app.route("/orders")
def orders():
    rows = list_orders()
    return render_template("orders.html", orders=rows)

# --------------------------------------------------
# API: CREATE ORDER (ESP32)
# --------------------------------------------------
@app.route("/api/create_order", methods=["POST"])
def create_order():
    require_esp32_auth()

    data = request.get_json(silent=True) or {}
    amount = int(data.get("amount", 0))

    if amount <= 0:
        return jsonify({"error": "Invalid amount"}), 400

    receipt = f"rcpt_{uuid.uuid4().hex[:10]}"

    order = razorpay_client.order.create({
        "amount": amount * 100,   # INR → paise
        "currency": "INR",
        "receipt": receipt,
        "payment_capture": 1
    })

    insert_order(order_id=order["id"], amount=amount)

    return jsonify({
        "order_id": order["id"],
        "amount": amount
    })

# --------------------------------------------------
# API: ORDER STATUS (ESP32 POLLING)
# --------------------------------------------------
@app.route("/api/order_status/<order_id>")
def order_status(order_id):
    require_esp32_auth()

    row = get_order(order_id)
    if not row:
        return jsonify({"error": "Order not found"}), 404

    return jsonify({
        "order_id": row["order_id"],
        "amount": row["amount"],
        "paid": bool(row["paid"]),
        "failed": bool(row["failed"]),
        "payment_id": row["payment_id"]
    })

# --------------------------------------------------
# WEBHOOK: RAZORPAY (SOURCE OF TRUTH)
# --------------------------------------------------
@app.route("/webhook/razorpay", methods=["POST"])
def razorpay_webhook():
    payload = request.data
    received_sig = request.headers.get("X-Razorpay-Signature", "")

    expected_sig = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(received_sig, expected_sig):
        abort(400)

    event = json.loads(payload)
    event_type = event.get("event")

    payment = event.get("payload", {}).get("payment", {}).get("entity", {})
    order_id = payment.get("order_id")
    payment_id = payment.get("id")

    if not order_id:
        return jsonify({"status": "ignored"})

    if event_type == "payment.captured":
        # ✅ DISPENSE ALLOWED
        mark_paid(order_id, payment_id)

    elif event_type == "payment.failed":
        # ❌ DO NOT DISPENSE
        mark_failed(order_id)

    return jsonify({"status": "ok"})

# --------------------------------------------------
# POLICY PAGES
# --------------------------------------------------
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

@app.route("/pay/<order_id>")
def pay_page(order_id):
    # Simple payment page that opens Razorpay Checkout
    return render_template(
        "pay.html",
        order_id=order_id,
        razorpay_key_id=RAZORPAY_KEY_ID
    )


# --------------------------------------------------
# MAIN
# --------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
