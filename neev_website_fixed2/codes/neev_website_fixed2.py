#!/usr/bin/env python3
# neev_complete_app.py
# Single-file Flask app: NEEV - Amazon-like prototype with admin add/edit/delete,
# cart, premium, UPI deep link, webhook placeholder, and SQLite persistence.
#
# DEMO: Card payments are mocked. For real payments configure a gateway (Razorpay/Cashfree/etc.)
# and set RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET environment vars.
#
# Run:
#   pip install flask requests
#   python neev_complete_app.py
#
import os
import sqlite3
import hmac
import hashlib
import json
from datetime import datetime
from functools import wraps
from flask import (
    Flask, g, session, redirect, url_for, request, render_template_string,
    flash, jsonify
)
import requests

# ------------------ CONFIG ------------------
APP_NAME = "NEEV - Lab Grown Diamonds"
ADMIN_PASSWORD = "2468"
UPI_ID = os.environ.get("NEEV_UPI_ID", "satishvimaljain@okhdfcbank")
MERCHANT_NAME = "NEEV"
CURRENCY = "INR"

BASE_DIR = os.path.dirname(__file__)
DATABASE = os.path.join(BASE_DIR, "neev_complete.db")
SECRET_KEY = os.environ.get("NEEV_SECRET", "change-this-secret-in-prod")

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

app = Flask(__name__)
app.config.update(SECRET_KEY=SECRET_KEY)

# ------------------ DB ------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    db = get_db()
    db.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT UNIQUE,
        name TEXT NOT NULL,
        category TEXT,
        description TEXT,
        price REAL NOT NULL,
        stock INTEGER NOT NULL DEFAULT 0,
        image TEXT
    );
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        external_id TEXT,
        created_at TEXT NOT NULL,
        customer_name TEXT,
        customer_email TEXT,
        customer_phone TEXT,
        address TEXT,
        premium INTEGER DEFAULT 0,
        total REAL NOT NULL,
        payment_method TEXT NOT NULL,
        payment_status TEXT NOT NULL DEFAULT 'pending',
        status TEXT NOT NULL DEFAULT 'created',
        gateway_payload TEXT
    );
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        product_id INTEGER,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        qty INTEGER NOT NULL,
        FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
    );
    """)
    db.commit()

def seed_products():
    db = get_db()
    c = db.execute("SELECT COUNT(*) as c FROM products").fetchone()["c"]
    if c == 0:
        sample = [
            ("NEEV-R01","Round Brilliant 1.0 ct","Rings","E/VS1 IGI Certified",24999.0,5,"https://images.unsplash.com/photo-1523292562811-8fa7962a78c8?q=80&w=1200&auto=format&fit=crop"),
            ("NEEV-P02","Princess Cut 0.75 ct","Pendants","F/VVS2 IGI Certified",19999.0,8,"https://images.unsplash.com/photo-1520962918319-47adfa87ee1b?q=80&w=1200&auto=format&fit=crop"),
            ("NEEV-O03","Oval 1.5 ct","Rings","D/VS2 IGI Certified",44999.0,2,"https://images.unsplash.com/photo-1516637090014-cb1ab0d08fc7?q=80&w=1200&auto=format&fit=crop"),
            ("NEEV-C04","Cushion 1.2 ct","Earrings","G/VS1 IGI Certified",28999.0,3,"https://images.unsplash.com/photo-1502082553048-f009c37129b9?q=80&w=1200&auto=format&fit=crop"),
        ]
        db.executemany(
            "INSERT INTO products (sku,name,category,description,price,stock,image) VALUES (?,?,?,?,?,?,?)",
            sample
        )
        db.commit()

# ------------------ HELPERS ------------------
def require_admin(view):
    @wraps(view)
    def _wrapped(*args, **kwargs):
        if session.get("is_admin"):
            return view(*args, **kwargs)
        return redirect(url_for("admin_login", next=request.path))
    return _wrapped

def format_currency(a):
    try:
        return f"₹{float(a):,.2f}"
    except:
        return f"₹{a}"

def cart_items():
    db = get_db()
    cart = session.get("cart", {})
    items = []
    for pid_s, qty in cart.items():
        try:
            pid = int(pid_s)
        except:
            continue
        row = db.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
        if row:
            items.append({
                "id": row["id"],
                "name": row["name"],
                "price": row["price"],
                "qty": qty,
                "subtotal": row["price"] * qty
            })
    return items

def cart_total(items=None, premium=False):
    items = items if items is not None else cart_items()
    subtotal = sum(i["subtotal"] for i in items)
    if premium:
        subtotal += 999.0
    return subtotal

# ------------------ GATEWAY HELPERS (Razorpay placeholder) ------------------
def gateway_create_order(amount_in_rupees, currency="INR", receipt=None):
    amount_paise = int(round(amount_in_rupees * 100))
    if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        return {
            "id": f"mock_order_{int(datetime.utcnow().timestamp())}",
            "amount": amount_paise,
            "currency": currency,
            "status": "created"
        }
    url = "https://api.razorpay.com/v1/orders"
    payload = {"amount": amount_paise, "currency": currency, "receipt": receipt or f"rcpt_{int(datetime.utcnow().timestamp())}"}
    resp = requests.post(url, auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET), json=payload, timeout=20)
    resp.raise_for_status()
    return resp.json()

def verify_razorpay_signature(body, signature, secret):
    if not secret:
        return False
    # secret should be bytes
    if isinstance(secret, str):
        secret = secret.encode('utf-8')
    computed = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)

# ------------------ ROUTES ------------------
@app.before_request
def ensure_db():
    init_db()
    seed_products()

@app.route("/")
def home():
    db = get_db()
    q = request.args.get("q","").strip()
    cat = request.args.get("cat","").strip()
    if q:
        products = db.execute("SELECT * FROM products WHERE name LIKE ? OR description LIKE ? LIMIT 200", (f"%{q}%", f"%{q}%")).fetchall()
    elif cat:
        products = db.execute("SELECT * FROM products WHERE category = ? LIMIT 200", (cat,)).fetchall()
    else:
        products = db.execute("SELECT * FROM products LIMIT 200").fetchall()
    categories = [r["category"] for r in db.execute("SELECT DISTINCT category FROM products").fetchall()]
    total = cart_total(cart_items(), premium=bool(session.get("premium")))
    return render_template_string(TPL_HOME, **locals())

@app.route("/product/<int:pid>")
def product_page(pid):
    db = get_db()
    p = db.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if not p:
        return "Product not found", 404
    return render_template_string(TPL_PRODUCT, **locals())

@app.route("/cart")
def cart():
    items = cart_items()
    total = cart_total(items, premium=bool(session.get("premium")))
    return render_template_string(TPL_CART, **locals())

@app.route("/cart/add/<int:pid>", methods=["POST"])
def cart_add(pid):
    qty = int(request.form.get("qty", 1))
    db = get_db()
    p = db.execute("SELECT id, stock FROM products WHERE id = ?", (pid,)).fetchone()
    if not p:
        flash("Product not found", "error")
        return redirect(url_for("home"))
    if qty < 1: qty = 1
    if qty > p["stock"]:
        flash("Quantity exceeds available stock", "error")
        return redirect(url_for("product_page", pid=pid))
    cart = session.setdefault("cart", {})
    cart[str(pid)] = cart.get(str(pid), 0) + qty
    session["cart"] = cart
    flash("Added to cart", "success")
    return redirect(url_for("cart"))

@app.route("/cart/update", methods=["POST"])
def cart_update():
    data = request.form
    cart = session.get("cart", {})
    for k,v in data.items():
        if k.startswith("qty_"):
            pid = k.split("_",1)[1]
            try:
                qty = int(v)
            except:
                qty = 0
            if qty <= 0:
                cart.pop(pid, None)
            else:
                cart[pid] = qty
    session["cart"] = cart
    session["premium"] = 1 if request.form.get("premium") == "on" else 0
    flash("Cart updated", "success")
    return redirect(url_for("cart"))

@app.route("/cart/remove/<int:pid>", methods=["POST"])
def cart_remove(pid):
    cart = session.get("cart", {})
    cart.pop(str(pid), None)
    session["cart"] = cart
    flash("Removed from cart", "success")
    return redirect(url_for("cart"))

@app.route("/checkout", methods=["GET","POST"])
def checkout():
    items = cart_items()
    premium = bool(session.get("premium"))
    if not items:
        flash("Your cart is empty", "error")
        return redirect(url_for("home"))
    total = cart_total(items, premium=premium)
    if request.method == "POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip()
        phone = request.form.get("phone","").strip()
        address = request.form.get("address","").strip()
        payment_method = request.form.get("payment_method","upi")
        if not name or not phone or not address:
            flash("Please fill required fields", "error")
            return redirect(url_for("checkout"))
        db = get_db()
        cur = db.execute("""INSERT INTO orders (created_at, customer_name, customer_email, customer_phone, address, premium, total, payment_method)
                            VALUES (?,?,?,?,?,?,?,?)""",
                         (datetime.utcnow().isoformat(), name, email, phone, address, int(premium), total, payment_method))
        order_id = cur.lastrowid
        for i in items:
            db.execute("INSERT INTO order_items (order_id, product_id, name, price, qty) VALUES (?,?,?,?,?)",
                       (order_id, i["id"], i["name"], i["price"], i["qty"]))
            db.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (i["qty"], i["id"]))
        db.commit()
        receipt = f"neev_rcpt_{order_id}"
        gateway_order = gateway_create_order(total, currency=CURRENCY, receipt=receipt)
        db.execute("UPDATE orders SET external_id = ?, gateway_payload = ? WHERE id = ?", (gateway_order.get("id"), json.dumps(gateway_order), order_id))
        db.commit()
        session["cart"] = {}
        session["premium"] = 0
        if payment_method == "upi":
            return redirect(url_for("upi_pay", order_id=order_id))
        else:
            # Demo: auto-mark paid when gateway keys are not configured
            if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
                db.execute("UPDATE orders SET payment_status='paid', status='confirmed' WHERE id = ?", (order_id,))
                db.commit()
            return redirect(url_for("order_status", order_id=order_id))
    return render_template_string(TPL_CHECKOUT, **locals())

@app.route("/upi/<int:order_id>")
def upi_pay(order_id):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return "Order not found", 404
    amount = order["total"]
    upi_link = f"upi://pay?pa={UPI_ID}&pn={MERCHANT_NAME}&am={amount:.2f}&cu={CURRENCY}&tn=Order%20{order_id}"
    return render_template_string(TPL_UPI, **locals())

@app.route("/order/<int:order_id>")
def order_status(order_id):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return "Order not found", 404
    items = db.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
    return render_template_string(TPL_ORDER_STATUS, **locals())

# ------------------ Help/Contact pages ------------------
@app.route("/help")
def help_center():
    return render_template_string(TPL_HELP, **locals())

@app.route("/contact")
def contact():
    return render_template_string(TPL_CONTACT, **locals())

# ------------------ ADMIN (add/edit/delete products) ------------------
@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method == "POST":
        pwd = request.form.get("password","")
        if pwd == ADMIN_PASSWORD:
            session["is_admin"] = True
            flash("Logged in as admin", "success")
            return redirect(request.args.get("next") or url_for("admin_dashboard"))
        flash("Wrong password", "error")
    return render_template_string(TPL_ADMIN_LOGIN, **locals())

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    flash("Logged out", "success")
    return redirect(url_for("home"))

@app.route("/admin")
@require_admin
def admin_dashboard():
    db = get_db()
    orders = db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 200").fetchall()
    products = db.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    return render_template_string(TPL_ADMIN, **locals())

@app.route("/admin/product/add", methods=["POST"])
@require_admin
def admin_product_add():
    name = request.form.get("name","").strip()
    price = float(request.form.get("price","0") or 0)
    stock = int(request.form.get("stock","0") or 0)
    description = request.form.get("description","").strip()
    category = request.form.get("category","General").strip()
    image = request.form.get("image","").strip()
    sku = request.form.get("sku","").strip() or f"SKU{int(datetime.utcnow().timestamp())}"
    if not name or price <= 0:
        flash("Enter valid product details", "error")
        return redirect(url_for("admin_dashboard"))
    db = get_db()
    db.execute("INSERT INTO products (sku,name,category,description,price,stock,image) VALUES (?,?,?,?,?,?,?)",
               (sku,name,category,description,price,stock,image))
    db.commit()
    flash("Product added", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/product/<int:pid>/edit", methods=["GET","POST"])
@require_admin
def admin_product_edit(pid):
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name","").strip()
        price = float(request.form.get("price","0") or 0)
        stock = int(request.form.get("stock","0") or 0)
        description = request.form.get("description","").strip()
        category = request.form.get("category","General").strip()
        image = request.form.get("image","").strip()
        if not name or price <= 0:
            flash("Enter valid details", "error")
            return redirect(url_for("admin_dashboard"))
        db.execute("UPDATE products SET name=?, category=?, description=?, price=?, stock=?, image=? WHERE id=?",
                   (name, category, description, price, stock, image, pid))
        db.commit()
        flash("Product updated", "success")
        return redirect(url_for("admin_dashboard"))
    p = db.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if not p:
        flash("Product not found", "error")
        return redirect(url_for("admin_dashboard"))
    return render_template_string(TPL_PRODUCT_EDIT, **locals())

@app.route("/admin/product/<int:pid>/delete", methods=["POST"])
@require_admin
def admin_product_delete(pid):
    db = get_db()
    db.execute("DELETE FROM products WHERE id = ?", (pid,))
    db.commit()
    flash("Product deleted", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/order/<int:order_id>/mark_paid", methods=["POST"])
@require_admin
def admin_mark_paid(order_id):
    db = get_db()
    db.execute("UPDATE orders SET payment_status='paid', status='confirmed' WHERE id = ?", (order_id,))
    db.commit()
    flash(f"Order {order_id} marked as paid", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/order/<int:order_id>/reject", methods=["POST"])
@require_admin
def admin_reject(order_id):
    db = get_db()
    db.execute("UPDATE orders SET payment_status='rejected', status='rejected' WHERE id = ?", (order_id,))
    db.commit()
    flash(f"Order {order_id} rejected", "error")
    return redirect(url_for("admin_dashboard"))

# ------------------ WEBHOOK ------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_data()
    signature = request.headers.get("X-Razorpay-Signature","")
    verified = False
    if RAZORPAY_WEBHOOK_SECRET:
        verified = verify_razorpay_signature(payload, signature, RAZORPAY_WEBHOOK_SECRET)
    else:
        if request.args.get("test") == "1":
            verified = True
    if not verified:
        app.logger.warning("Webhook signature not verified")
        return "signature mismatch", 400
    event = request.json or {}
    try:
        ev = event.get("event") or event.get("type") or ""
        if ev in ("payment.captured","payment.captured.v1"):
            payment = (event.get("payload") or {}).get("payment",{}) or {}
            entity = payment.get("entity") if isinstance(payment, dict) else None
            gateway_order_id = None
            if entity:
                gateway_order_id = entity.get("order_id") or entity.get("id")
            db = get_db()
            if gateway_order_id:
                db.execute("UPDATE orders SET payment_status='paid', status='confirmed', gateway_payload = ? WHERE external_id = ?",
                           (json.dumps(entity), gateway_order_id))
                db.commit()
            return jsonify({"ok": True})
        else:
            # store event for debugging (optional)
            db = get_db()
            db.execute("INSERT INTO orders (created_at, customer_name, total, payment_method, payment_status, status, gateway_payload) VALUES (?,?,?,?,?,?,?)",
                       (datetime.utcnow().isoformat(), "webhook_event", 0.0, "webhook", "pending", "webhook", json.dumps(event)))
            db.commit()
            return jsonify({"ok": True})
    except Exception:
        app.logger.exception("Webhook handling failed")
        return "error", 500

# ------------------ TEMPLATES ------------------
BASE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ APP_NAME }}</title>
<link href="https://cdn.jsdelivr.net/npm/bulma@0.9.4/css/bulma.min.css" rel="stylesheet">
<style>.container{max-width:1100px}.brand{font-weight:800}</style>
</head>
<body>
<nav class="navbar is-white"><div class="container">
  <div class="navbar-brand"><a class="navbar-item brand" href="{{ url_for('home') }}">{{ APP_NAME }}</a>
    <a role="button" class="navbar-burger" data-target="navMenu"><span></span><span></span><span></span></a>
  </div>
  <div id="navMenu" class="navbar-menu">
    <div class="navbar-start">
      <form action="{{ url_for('home') }}" class="navbar-item">
        <div class="field has-addons"><div class="control"><input class="input" name="q" placeholder="Search diamonds..."></div>
        <div class="control"><button class="button is-info">Search</button></div></div>
      </form>
    </div>
    <div class="navbar-end">
      <a class="navbar-item" href="{{ url_for('help_center') }}">Help</a>
      <a class="navbar-item" href="{{ url_for('contact') }}">Contact</a>
      {% if session.get('is_admin') %}
        <a class="navbar-item" href="{{ url_for('admin_dashboard') }}">Admin</a>
        <a class="navbar-item" href="{{ url_for('admin_logout') }}">Logout</a>
      {% else %}
        <a class="navbar-item" href="{{ url_for('admin_login') }}">Admin</a>
      {% endif %}
      <a class="navbar-item" href="{{ url_for('cart') }}">Cart 🛒</a>
    </div>
  </div>
</div></nav>

<section class="section"><div class="container">
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      {% for category, message in messages %}
        <div class="notification is-{{ 'danger' if category=='error' else category }}">{{ message }}</div>
      {% endfor %}
    {% endif %}
  {% endwith %}
  {% block content %}{% endblock %}
</div></section>

<footer class="footer"><div class="content has-text-centered">
  <p><strong>{{ APP_NAME }}</strong> — Demo store. UPI ID: <code>{{ UPI_ID }}</code></p>
</div></footer>

<script>
document.addEventListener('DOMContentLoaded', () => {
 const burger = document.querySelector('.navbar-burger');
 const menu = document.getElementById('navMenu');
 if (burger && menu) burger.addEventListener('click', ()=>{ burger.classList.toggle('is-active'); menu.classList.toggle('is-active'); });
});
</script>
</body>
</html>
"""

TPL_HOME = r"""{% extends "BASE" %}{% block content %}
<div class="columns">
  <div class="column is-3">
    <aside class="menu">
      <p class="menu-label">Categories</p>
      <ul class="menu-list"><li><a href="{{ url_for('home') }}">All</a></li>{% for c in categories %}<li><a href="{{ url_for('home') }}?cat={{ c }}">{{ c }}</a></li>{% endfor %}</ul>
      <p class="menu-label">NEEV Premium</p>
      <div class="box"><p><strong>NEEV Premium</strong></p><p>Fast delivery, exclusive offers, priority support.</p><p class="mt-2"><strong>₹999</strong></p></div>
    </aside>
  </div>
  <div class="column is-9">
    <h1 class="title">NEEV — Lab Grown Diamonds</h1>
    <div class="columns is-multiline">
      {% for p in products %}
        <div class="column is-4">
          <div class="card">
            {% if p.image %}<div class="card-image"><figure class="image is-4by3"><img src="{{ p.image }}"></figure></div>{% endif %}
            <div class="card-content">
              <p class="title is-5">{{ p.name }}</p><p class="subtitle is-6">{{ p.description }}</p>
              <p class="is-price">{{ format_currency(p.price) }}</p>
              {% if p.stock > 0 %}<span class="tag is-success">In stock: {{ p.stock }}</span>{% else %}<span class="tag is-danger">Unavailable</span>{% endif %}
            </div>
            <footer class="card-footer">
              <form action="{{ url_for('cart_add', pid=p.id) }}" method="post" class="card-footer-item"><input type="hidden" name="qty" value="1"><button class="button is-small is-primary">Add to cart</button></form>
              <a class="card-footer-item" href="{{ url_for('product_page', pid=p.id) }}">View</a>
            </footer>
          </div>
        </div>
      {% endfor %}
    </div>
  </div>
</div>
{% endblock %}"""

TPL_PRODUCT = r"""{% extends "BASE" %}{% block content %}
<div class="columns">
  <div class="column is-6">{% if p.image %}<figure class="image is-4by3"><img src="{{ p.image }}"></figure>{% endif %}</div>
  <div class="column is-6">
    <h2 class="title">{{ p.name }}</h2><p class="subtitle">{{ p.description }}</p>
    <p class="is-price">{{ format_currency(p.price) }}</p>
    {% if p.stock>0 %}
      <form method="post" action="{{ url_for('cart_add', pid=p.id) }}"><div class="field has-addons mt-4"><div class="control"><input class="input" type="number" name="qty" min="1" value="1"></div><div class="control"><button class="button is-primary">Add to cart</button></div></div></form>
    {% else %}<span class="tag is-danger">Unavailable</span>{% endif %}
  </div>
</div>
{% endblock %}"""

TPL_CART = r"""{% extends "BASE" %}{% block content %}
<h1 class="title">Your Cart</h1>
<form method="post" action="{{ url_for('cart_update') }}">
<table class="table is-fullwidth">
  <thead><tr><th>Product</th><th>Qty</th><th>Price</th><th>Subtotal</th><th></th></tr></thead><tbody>
  {% for i in items %}
    <tr>
      <td>{{ i.name }}</td>
      <td><input class="input" style="width:80px" type="number" name="qty_{{ i.id }}" value="{{ i.qty }}" min="1"></td>
      <td>{{ format_currency(i.price) }}</td>
      <td>{{ format_currency(i.subtotal) }}</td>
      <td><form method="post" action="{{ url_for('cart_remove', pid=i.id) }}"><button class="button is-small">Remove</button></form></td>
    </tr>
  {% endfor %}
  </tbody>
</table>

<label class="checkbox"><input type="checkbox" name="premium" {% if session.get('premium') %}checked{% endif %}> Add NEEV Premium (₹999)</label>
<p class="is-size-5"><strong>Total: {{ format_currency(total) }}</strong></p>
<button class="button is-primary" type="submit">Proceed to Checkout</button>
</form>
{% endblock %}"""

TPL_CHECKOUT = r"""{% extends "BASE" %}{% block content %}
<h1 class="title">Checkout</h1>
<form method="post">
  <div class="columns">
    <div class="column is-6">
      <h2 class="subtitle">Shipping Details</h2>
      <div class="field"><label class="label">Full Name*</label><div class="control"><input class="input" name="name" required></div></div>
      <div class="field"><label class="label">Email</label><div class="control"><input class="input" name="email" type="email"></div></div>
      <div class="field"><label class="label">Phone*</label><div class="control"><input class="input" name="phone" required></div></div>
      <div class="field"><label class="label">Address*</label><div class="control"><textarea class="textarea" name="address" required></textarea></div></div>
    </div>
    <div class="column is-6">
      <h2 class="subtitle">Payment</h2>
      <div class="field"><label class="radio"><input type="radio" name="payment_method" value="card" checked> Card</label> <label class="radio"><input type="radio" name="payment_method" value="upi"> UPI</label></div>
      <div id="card_fields">
        <div class="field"><label class="label">Card Number</label><div class="control"><input class="input" name="card_number" maxlength="19" placeholder="1111 2222 3333 4444"></div></div>
        <div class="field is-grouped"><div class="control is-expanded"><label class="label">Expiry (MM/YY)</label><input class="input" name="expiry" placeholder="12/28"></div><div class="control is-expanded"><label class="label">CVV</label><input class="input" name="cvv" maxlength="4" placeholder="123"></div></div>
        <p class="help">Demo only. Configure a gateway for real card processing.</p>
      </div>
      <div id="upi_hint" style="display:none;"><article class="message is-info"><div class="message-body">We'll open a UPI payment link. UPI ID: <strong>{{ UPI_ID }}</strong>.</div></article></div>
      <p class="is-size-5"><strong>Payable: {{ format_currency(total) }}</strong></p>
      <button class="button is-primary" type="submit">Place Order</button>
    </div>
  </div>
</form>
<script>
const radios = document.getElementsByName('payment_method');
function updatePayUI(){ const card=document.getElementById('card_fields'); const upi=document.getElementById('upi_hint'); const val = Array.from(radios).find(r=>r.checked)?.value; if(val==='upi'){card.style.display='none';upi.style.display='block'}else{card.style.display='block';upi.style.display='none'} }
radios.forEach(r=>r.addEventListener('change', updatePayUI)); updatePayUI();
</script>
{% endblock %}"""

TPL_UPI = r"""{% extends "BASE" %}{% block content %}
<h1 class="title">Pay via UPI</h1>
<p>Order <strong>#{{ order['id'] }}</strong> • Amount <strong>{{ format_currency(order['total']) }}</strong></p>
<p>UPI ID: <code>{{ UPI_ID }}</code></p>
<div class="buttons mt-4"><a class="button is-link" href="{{ upi_link }}">Open UPI Apps</a></div>
<article class="message"><div class="message-body">After completing payment, click below to check status.</div></article>
<a class="button is-primary" href="{{ url_for('order_status', order_id=order['id']) }}">I have paid — Check Status</a>
{% endblock %}"""

TPL_ORDER_STATUS = r"""{% extends "BASE" %}{% block content %}
<h1 class="title">Order Status</h1>
<p>Order <strong>#{{ order['id'] }}</strong> — Created {{ order['created_at'] }}</p>
<p>Payment: <strong>{{ order['payment_status'].upper() }}</strong> • Status: <strong>{{ order['status'].upper() }}</strong></p>
{% if order['payment_status']=='paid' and order['status'] in ('confirmed','fulfilled') %}
  <article class="message is-success"><div class="message-body">Order placed ✅ — Thank you!</div></article>
{% elif order['payment_status']=='rejected' or order['status']=='rejected' %}
  <article class="message is-danger"><div class="message-body">Your order was rejected. Contact support.</div></article>
{% else %}
  <article class="message is-warning"><div class="message-body">Awaiting payment confirmation. If you paid via UPI, admin will confirm once received.</div></article>
{% endif %}
<h2 class="subtitle mt-4">Items</h2>
<table class="table is-fullwidth"><thead><tr><th>Product</th><th>Qty</th><th>Price</th><th>Subtotal</th></tr></thead><tbody>
{% for i in items %}<tr><td>{{ i['name'] }}</td><td>{{ i['qty'] }}</td><td>{{ format_currency(i['price']) }}</td><td>{{ format_currency(i['price']*i['qty']) }}</td></tr>{% endfor %}
</tbody></table>
<p class="is-size-5"><strong>Total: {{ format_currency(order['total']) }}</strong></p>
<a class="button mt-3" href="{{ url_for('home') }}">Back to Home</a>
{% endblock %}"""

TPL_ADMIN_LOGIN = r"""{% extends "BASE" %}{% block content %}
<h1 class="title">Admin Login</h1>
<form method="post"><div class="field"><label class="label">Password</label><div class="control"><input class="input" name="password" type="password"></div></div><button class="button is-primary" type="submit">Login</button></form>
{% endblock %}"""

TPL_PRODUCT_EDIT = r"""{% extends "BASE" %}{% block content %}
<h1 class="title">Edit Product</h1>
<form method="post">
  <div class="field"><label class="label">Name</label><div class="control"><input class="input" name="name" value="{{ p['name'] }}"></div></div>
  <div class="field"><label class="label">Category</label><div class="control"><input class="input" name="category" value="{{ p['category'] }}"></div></div>
  <div class="field"><label class="label">Description</label><div class="control"><textarea class="textarea" name="description">{{ p['description'] }}</textarea></div></div>
  <div class="field"><label class="label">Price</label><div class="control"><input class="input" name="price" type="number" step="0.01" value="{{ p['price'] }}"></div></div>
  <div class="field"><label class="label">Stock</label><div class="control"><input class="input" name="stock" type="number" value="{{ p['stock'] }}"></div></div>
  <div class="field"><label class="label">Image URL</label><div class="control"><input class="input" name="image" value="{{ p['image'] }}"></div></div>
  <button class="button is-primary" type="submit">Save</button>
  <a class="button" href="{{ url_for('admin_dashboard') }}">Cancel</a>
</form>
{% endblock %}"""

TPL_ADMIN = r"""{% extends "BASE" %}{% block content %}
<h1 class="title">Admin Dashboard</h1>
<h2 class="subtitle">Orders</h2>
<table class="table is-fullwidth"><thead><tr><th>ID</th><th>Created</th><th>Customer</th><th>Total</th><th>Pay</th><th>Status</th><th>Actions</th></tr></thead><tbody>
{% for o in orders %}
  <tr>
    <td>#{{ o['id'] }}</td><td>{{ o['created_at'] }}</td>
    <td>{{ o['customer_name'] }}<br><small>{{ o['customer_phone'] }}</small></td>
    <td>{{ format_currency(o['total']) }}</td><td>{{ o['payment_status'] }}</td><td>{{ o['status'] }}</td>
    <td>
      <form style="display:inline" method="post" action="{{ url_for('admin_mark_paid', order_id=o['id']) }}"><button class="button is-small is-success">Mark Paid</button></form>
      <form style="display:inline" method="post" action="{{ url_for('admin_reject', order_id=o['id']) }}"><button class="button is-small is-danger">Reject</button></form>
    </td>
  </tr>
{% endfor %}
</tbody></table>
<h2 class="subtitle mt-5">Add Product</h2>
<form method="post" action="{{ url_for('admin_product_add') }}" class="box">
  <div class="field"><label class="label">SKU (optional)</label><div class="control"><input class="input" name="sku"></div></div>
  <div class="field"><label class="label">Name</label><div class="control"><input class="input" name="name" required></div></div>
  <div class="field"><label class="label">Category</label><div class="control"><input class="input" name="category"></div></div>
  <div class="field"><label class="label">Description</label><div class="control"><input class="input" name="description"></div></div>
  <div class="field is-grouped"><div class="control is-expanded"><label class="label">Price</label><input class="input" name="price" type="number" step="0.01" required></div><div class="control is-expanded"><label class="label">Stock</label><input class="input" name="stock" type="number" required></div></div>
  <div class="field"><label class="label">Image URL</label><div class="control"><input class="input" name="image"></div></div>
  <button class="button is-primary" type="submit">Add Product</button>
</form>
<div class="mt-4">{% for p in products %}<div class="box"><div class="columns is-vcentered"><div class="column is-2">{% if p['image'] %}<figure class="image is-64x64"><img src="{{ p['image'] }}"></figure>{% endif %}</div><div class="column"><strong>{{ p['name'] }}</strong><br><small>{{ p['category'] }} • {{ format_currency(p['price']) }}</small></div><div class="column is-narrow"><a class="button is-small" href="{{ url_for('admin_product_edit', pid=p['id']) }}">Edit</a> <form method="post" action="{{ url_for('admin_product_delete', pid=p['id']) }}" style="display:inline" onsubmit="return confirm('Delete product?');"><button class="button is-small is-danger">Delete</button></form></div></div></div>{% endfor %}</div>
{% endblock %}"""

TPL_HELP = r"""{% extends "BASE" %}{% block content %}
<h1 class="title">Help & Support</h1>
<div class="content">
  <h3>Common Issues</h3>
  <ul>
    <li><strong>Order didn't arrive</strong> — Check your order status page. If it's fulfilled but not delivered, contact us with your order ID.</li>
    <li><strong>Payment failed</strong> — For card, verify number/CVV/expiry (demo flow). For UPI, ensure you approved the request in your app.</li>
    <li><strong>Want refund/return?</strong> — Contact support@neev.example with order details.</li>
  </ul>
</div>
{% endblock %}"""

TPL_CONTACT = r"""{% extends "BASE" %}{% block content %}
<h1 class="title">Contact Us</h1>
<div class="box">
  <p>Email: neev0509@gmail.com</p>
  <p>Phone: +91-70450-21529</p>
</div>
{% endblock %}"""

from jinja2 import DictLoader
app.jinja_loader = DictLoader({
    "BASE": BASE,
    "home.html": TPL_HOME
})
app.jinja_env.globals.update(format_currency=format_currency, APP_NAME=APP_NAME, UPI_ID=UPI_ID)

# ------------------ RUN ------------------
if __name__ == "__main__":
    with app.app_context():
        init_db()
        seed_products()
    app.run(debug=True)