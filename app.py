import os
import uuid
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
ESP32_BEARER_TOKEN = os.getenv("ESP32_BEARER_TOKEN")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev_secret")

if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
    raise RuntimeError("Missing Razorpay keys")

if not ESP32_BEARER_TOKEN:
    raise RuntimeError("Missing ESP32 bearer token")

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
            "paid": r[4],
            "payment_id": r[5],
            "created_at": r[6]
        })

    return render_template("orders.html", orders=formatted)


# --------------------------------------------------
# API: CREATE PAYMENT URI
# --------------------------------------------------

@app.route("/api/create_payment_uri", methods=["POST"])
def create_payment_uri():
    require_esp32_auth()

    data = request.get_json(silent=True) or {}
    amount = int(data.get("amount", 0))

    if amount <= 0:
        return jsonify({"error": "Invalid amount"}), 400

    # Create Razorpay order
    order = razorpay_client.order.create({
        "amount": amount * 100,
        "currency": "INR",
        "receipt": f"rcpt_{uuid.uuid4().hex[:8]}"
    })

    # Create payment link (URI)
    link = razorpay_client.payment_link.create({
        "amount": amount * 100,
        "currency": "INR",
        "reference_id": order["id"],
        "description": "Automated Vending Machine Payment",
        "notes": {
            "order_id": order["id"]
        }
    })

    # Store in DB
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
# API: PAYMENT STATUS
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
# MAIN
# --------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
