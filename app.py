import os
import sqlite3
from flask import Flask, render_template, redirect, url_for, session, request, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

basedir = os.path.abspath(os.path.dirname(__file__))
# DB_PATH es configurable por variable de entorno para poder apuntar a un
# volumen persistente en Railway (ver README para instrucciones de montaje).
DB_PATH = os.environ.get('DB_PATH', os.path.join(basedir, 'teukit.db'))

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'cambia-esta-clave-en-produccion')


# ----------------------------
# CONEXIÓN A LA BASE DE DATOS
# ----------------------------

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute('''
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS product (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            price_cents INTEGER NOT NULL,
            description TEXT NOT NULL,
            image TEXT NOT NULL,
            stock INTEGER DEFAULT 100,
            active INTEGER DEFAULT 1
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS "order" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            customer_name TEXT NOT NULL,
            customer_email TEXT NOT NULL,
            customer_address TEXT NOT NULL,
            customer_phone TEXT,
            total_cents INTEGER NOT NULL,
            status TEXT DEFAULT 'pendiente',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS order_item (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            unit_price_cents INTEGER NOT NULL,
            quantity INTEGER NOT NULL
        )
    ''')

    existing = db.execute('SELECT COUNT(*) FROM product').fetchone()[0]
    if existing == 0:
        db.execute('''
            INSERT INTO product (name, slug, price_cents, description, image, stock, active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            "KIT Essencial - Primeiros Socorros",
            "kit-essencial",
            3990,
            "O kit inclui: Mochila, Kit de primeiros socorros, Lanterna, "
            "Máscaras, Apito, Água e snacks, Manta térmica e Folhetos "
            "informativos com orientações sobre como agir em caso de emergência.",
            "kit-essencial.jpg",
            100,
            1
        ))
        print("Producto inicial 'KIT Essencial' creado.")

    db.commit()
    db.close()


def price_display(cents):
    return f"{cents/100:.2f}".replace('.', ',') + " €"


# ----------------------------
# FUNCIONES DEL CARRITO (guardado en sesión)
# ----------------------------

def get_cart():
    return session.get('cart', {})


def save_cart(cart):
    session['cart'] = cart
    session.modified = True


def get_cart_items():
    db = get_db()
    cart = get_cart()
    items = []
    total_cents = 0
    for product_id, qty in cart.items():
        product = db.execute('SELECT * FROM product WHERE id = ?', (product_id,)).fetchone()
        if not product:
            continue
        subtotal_cents = product['price_cents'] * qty
        total_cents += subtotal_cents
        items.append({
            'product': product,
            'quantity': qty,
            'subtotal_display': price_display(subtotal_cents)
        })
    return items, total_cents


app.jinja_env.globals.update(price_display=price_display)

# Contraseña del panel de administración.
# En Railway, configúrala como variable de entorno ADMIN_PASSWORD para no
# dejarla escrita en el código. Localmente, si no la defines, usa esta por
# defecto SOLO para pruebas.
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'teukit2026')


def admin_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('admin_login'))
        return view_func(*args, **kwargs)
    return wrapped


def get_current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    db = get_db()
    return db.execute('SELECT * FROM user WHERE id = ?', (user_id,)).fetchone()


def login_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get('user_id'):
            flash('Precisas de iniciar sessão para aceder a esta página.', 'error')
            return redirect(url_for('login', next=request.path))
        return view_func(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_current_user():
    return {'current_user': get_current_user()}


# ----------------------------
# RUTAS
# ----------------------------

@app.route('/')
def home():
    db = get_db()
    products = db.execute('SELECT * FROM product WHERE active = 1').fetchall()
    return render_template('home.html', products=products)


@app.route('/producto/<slug>')
def product_detail(slug):
    db = get_db()
    product = db.execute('SELECT * FROM product WHERE slug = ? AND active = 1', (slug,)).fetchone()
    if not product:
        return "Producto no encontrado", 404
    return render_template('product_detail.html', product=product)


@app.route('/guia')
def guide():
    return render_template('guide.html')


@app.route('/carrito/agregar/<int:product_id>', methods=['POST'])
def cart_add(product_id):
    db = get_db()
    product = db.execute('SELECT * FROM product WHERE id = ?', (product_id,)).fetchone()
    if not product:
        return "Producto no encontrado", 404
    if product['stock'] <= 0:
        flash(f'"{product["name"]}" está esgotado.', 'error')
        return redirect(request.referrer or url_for('home'))
    qty = int(request.form.get('quantity', 1))
    cart = get_cart()
    key = str(product_id)
    new_qty = cart.get(key, 0) + qty
    if new_qty > product['stock']:
        new_qty = product['stock']
        flash(f'Apenas {product["stock"]} unidades disponíveis de "{product["name"]}".', 'error')
    cart[key] = new_qty
    save_cart(cart)
    flash(f'"{product["name"]}" añadido al carrito.', 'success')
    return redirect(request.referrer or url_for('home'))


@app.route('/carrito/quitar/<int:product_id>')
def cart_remove(product_id):
    cart = get_cart()
    cart.pop(str(product_id), None)
    save_cart(cart)
    return redirect(url_for('cart_view'))


@app.route('/carrito/actualizar/<int:product_id>', methods=['POST'])
def cart_update(product_id):
    qty = int(request.form.get('quantity', 1))
    cart = get_cart()
    if qty <= 0:
        cart.pop(str(product_id), None)
    else:
        cart[str(product_id)] = qty
    save_cart(cart)
    return redirect(url_for('cart_view'))


@app.route('/carrito')
def cart_view():
    items, total_cents = get_cart_items()
    return render_template('cart.html', items=items, total_display=price_display(total_cents), is_empty=(len(items) == 0))


@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    items, total_cents = get_cart_items()
    if not items:
        flash('Tu carrito está vacío.', 'error')
        return redirect(url_for('home'))

    current_user = get_current_user()

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        address = request.form.get('address', '').strip()
        phone = request.form.get('phone', '').strip()

        if not name or not email or not address:
            flash('Por favor completa nombre, email y dirección.', 'error')
            return render_template('checkout.html', items=items, total_display=price_display(total_cents), current_user=current_user)

        db = get_db()
        user_id = current_user['id'] if current_user else None
        cursor = db.execute('''
            INSERT INTO "order" (user_id, customer_name, customer_email, customer_address, customer_phone, total_cents, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pendiente')
        ''', (user_id, name, email, address, phone, total_cents))
        order_id = cursor.lastrowid

        for item in items:
            db.execute('''
                INSERT INTO order_item (order_id, product_id, product_name, unit_price_cents, quantity)
                VALUES (?, ?, ?, ?, ?)
            ''', (order_id, item['product']['id'], item['product']['name'],
                  item['product']['price_cents'], item['quantity']))

        db.commit()

        # --- INTEGRACIÓN DE PAGO (PLACEHOLDER) ---
        # Aquí es donde se conecta Stripe cuando tengas tu cuenta creada.
        # Ver create_stripe_checkout_session() comentada más abajo.
        # De momento el pedido queda registrado como "pendiente".

        save_cart({})
        return redirect(url_for('order_confirmation', order_id=order_id))

    return render_template('checkout.html', items=items, total_display=price_display(total_cents), current_user=current_user)


@app.route('/pedido/<int:order_id>/confirmacion')
def order_confirmation(order_id):
    db = get_db()
    order = db.execute('SELECT * FROM "order" WHERE id = ?', (order_id,)).fetchone()
    if not order:
        return "Pedido no encontrado", 404
    return render_template('order_confirmation.html', order=order)


# ----------------------------
# CUENTAS DE CLIENTE
# ----------------------------

@app.route('/registro', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not name or not email or not password:
            flash('Por favor preenche todos os campos.', 'error')
            return render_template('register.html')

        if len(password) < 6:
            flash('A palavra-passe deve ter pelo menos 6 caracteres.', 'error')
            return render_template('register.html')

        db = get_db()
        existing = db.execute('SELECT id FROM user WHERE email = ?', (email,)).fetchone()
        if existing:
            flash('Já existe uma conta com este email.', 'error')
            return render_template('register.html')

        password_hash = generate_password_hash(password)
        cursor = db.execute(
            'INSERT INTO user (name, email, password_hash) VALUES (?, ?, ?)',
            (name, email, password_hash)
        )
        db.commit()
        session['user_id'] = cursor.lastrowid
        flash(f'Bem-vindo(a), {name}!', 'success')
        return redirect(url_for('home'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        db = get_db()
        user = db.execute('SELECT * FROM user WHERE email = ?', (email,)).fetchone()

        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            flash(f'Bem-vindo(a) de volta, {user["name"]}!', 'success')
            next_url = request.args.get('next') or url_for('home')
            return redirect(next_url)

        flash('Email ou palavra-passe incorretos.', 'error')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('home'))


@app.route('/minha-conta')
@login_required
def my_account():
    db = get_db()
    user = get_current_user()
    orders = db.execute(
        'SELECT * FROM "order" WHERE user_id = ? ORDER BY created_at DESC', (user['id'],)
    ).fetchall()

    orders_with_items = []
    for order in orders:
        items = db.execute('SELECT * FROM order_item WHERE order_id = ?', (order['id'],)).fetchall()
        orders_with_items.append({'order': order, 'order_items': items})

    return render_template('my_account.html', user=user, orders_with_items=orders_with_items)


# ----------------------------
# PANEL DE ADMINISTRACIÓN (PEDIDOS)
# ----------------------------

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect(url_for('admin_orders'))
        flash('Contraseña incorrecta.', 'error')
    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_login'))


@app.route('/admin/pedidos')
@admin_required
def admin_orders():
    db = get_db()
    orders = db.execute('SELECT * FROM "order" ORDER BY created_at DESC').fetchall()

    orders_with_items = []
    for order in orders:
        items = db.execute('SELECT * FROM order_item WHERE order_id = ?', (order['id'],)).fetchall()
        orders_with_items.append({'order': order, 'order_items': items})

    return render_template('admin_orders.html', orders_with_items=orders_with_items)


@app.route('/admin/pedidos/<int:order_id>/estado', methods=['POST'])
@admin_required
def admin_update_status(order_id):
    new_status = request.form.get('status')
    if new_status not in ('pendiente', 'pagado', 'enviado'):
        flash('Estado no válido.', 'error')
        return redirect(url_for('admin_orders'))

    db = get_db()
    db.execute('UPDATE "order" SET status = ? WHERE id = ?', (new_status, order_id))
    db.commit()
    flash(f'Pedido #{order_id} actualizado a "{new_status}".', 'success')
    return redirect(url_for('admin_orders'))


@app.route('/admin/produtos')
@admin_required
def admin_products():
    db = get_db()
    products = db.execute('SELECT * FROM product ORDER BY id').fetchall()
    return render_template('admin_products.html', products=products)


@app.route('/admin/produtos/<int:product_id>/editar', methods=['POST'])
@admin_required
def admin_update_product(product_id):
    stock = request.form.get('stock', '').strip()
    description = request.form.get('description', '').strip()
    price_str = request.form.get('price', '').strip()

    if not stock.isdigit():
        flash('El stock debe ser un número válido.', 'error')
        return redirect(url_for('admin_products'))

    if not description:
        flash('La descripción no puede estar vacía.', 'error')
        return redirect(url_for('admin_products'))

    try:
        price_cents = round(float(price_str.replace(',', '.')) * 100)
    except ValueError:
        flash('El precio no es válido.', 'error')
        return redirect(url_for('admin_products'))

    db = get_db()
    db.execute(
        'UPDATE product SET stock = ?, description = ?, price_cents = ? WHERE id = ?',
        (int(stock), description, price_cents, product_id)
    )
    db.commit()
    flash('Producto actualizado correctamente.', 'success')
    return redirect(url_for('admin_products'))


# ----------------------------
# PLACEHOLDER PARA STRIPE (activar cuando tengas cuenta)
# ----------------------------
#
# 1. pip install stripe   (y añádelo a requirements.txt)
# 2. Crear cuenta en https://stripe.com y obtener tu clave secreta (API key)
# 3. Configurar variable de entorno STRIPE_SECRET_KEY en Railway
# 4. Descomentar el siguiente bloque y llamarlo desde /checkout antes de
#    redirigir a order_confirmation, redirigiendo en su lugar a la URL
#    que devuelve Stripe (checkout_session.url)
#
# import stripe
# stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
#
# def create_stripe_checkout_session(items, order_id):
#     line_items = [{
#         'price_data': {
#             'currency': 'eur',
#             'product_data': {'name': item['product']['name']},
#             'unit_amount': item['product']['price_cents'],
#         },
#         'quantity': item['quantity'],
#     } for item in items]
#
#     checkout_session = stripe.checkout.Session.create(
#         payment_method_types=['card'],
#         line_items=line_items,
#         mode='payment',
#         success_url=url_for('order_confirmation', order_id=order_id, _external=True),
#         cancel_url=url_for('cart_view', _external=True),
#     )
#     return checkout_session


init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
