import sqlite3

DB_NAME = "orders.db"

def _conn():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = _conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            amount INTEGER,
            paid INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            payment_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def insert_order(order_id, amount):
    conn = _conn()
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO orders (order_id, amount)
        VALUES (?, ?)
    """, (order_id, amount))
    conn.commit()
    conn.close()

def mark_paid(order_id, payment_id):
    conn = _conn()
    c = conn.cursor()
    c.execute("""
        UPDATE orders
        SET paid=1, failed=0, payment_id=?
        WHERE order_id=?
    """, (payment_id, order_id))
    conn.commit()
    conn.close()

def mark_failed(order_id):
    conn = _conn()
    c = conn.cursor()
    c.execute("""
        UPDATE orders
        SET failed=1
        WHERE order_id=?
    """, (order_id,))
    conn.commit()
    conn.close()

def get_order(order_id):
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE order_id=?", (order_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def list_orders():
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT * FROM orders ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]
