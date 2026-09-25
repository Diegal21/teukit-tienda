import os
import sqlite3
import threading
import re
import io
import uuid
import requests
from flask import Flask, render_template, redirect, url_for, session, request, flash, g, send_from_directory, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from translations import translate
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

basedir = os.path.abspath(os.path.dirname(__file__))
# DB_PATH es configurable por variable de entorno para poder apuntar a un
# volumen persistente en Railway (ver README para instrucciones de montaje).
DB_PATH = os.environ.get('DB_PATH', os.path.join(basedir, 'teukit.db'))

# Las imágenes subidas desde el panel de admin se guardan en el mismo
# volumen persistente que la base de datos, para que no se pierdan con
# cada despliegue.
UPLOAD_FOLDER = os.path.join(os.path.dirname(DB_PATH), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'cambia-esta-clave-en-produccion')
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB máximo por imagen


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
            active INTEGER DEFAULT 1,
            category TEXT DEFAULT 'kit_emergencia',
            discount_percent INTEGER DEFAULT 0
        )
    ''')
    # Migración: si el producto ya existía sin columna 'category' (bases de
    # datos creadas antes de agregar el catálogo por categorías), la agrega.
    try:
        db.execute("ALTER TABLE product ADD COLUMN category TEXT DEFAULT 'kit_emergencia'")
    except sqlite3.OperationalError:
        pass  # la columna ya existe
    # Migración: agrega la columna de descuento promocional si no existe.
    try:
        db.execute("ALTER TABLE product ADD COLUMN discount_percent INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # la columna ya existe
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
            INSERT INTO product (name, slug, price_cents, description, image, stock, active, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            "KIT Essencial - Primeiros Socorros",
            "kit-essencial",
            3990,
            "O kit inclui: Mochila, Kit de primeiros socorros, Lanterna, "
            "Máscaras, Apito, Água e snacks, Manta térmica e Folhetos "
            "informativos com orientações sobre como agir em caso de emergência.",
            "kit-essencial.jpg",
            100,
            1,
            "kit_emergencia"
        ))
        print("Producto inicial 'KIT Essencial' creado.")

    db.commit()
    db.close()


def price_display(cents):
    return f"{cents/100:.2f}".replace('.', ',') + " €"


def has_discount(product):
    return bool(product['discount_percent']) and product['discount_percent'] > 0


def effective_price_cents(product):
    """Precio final a cobrar: aplica el descuento promocional si existe."""
    if has_discount(product):
        return round(product['price_cents'] * (100 - product['discount_percent']) / 100)
    return product['price_cents']


def slugify(text):
    text = text.lower().strip()
    replacements = {
        'á': 'a', 'à': 'a', 'ã': 'a', 'â': 'a',
        'é': 'e', 'ê': 'e',
        'í': 'i',
        'ó': 'o', 'ô': 'o', 'õ': 'o',
        'ú': 'u', 'ü': 'u',
        'ç': 'c', 'ñ': 'n',
    }
    for accented, plain in replacements.items():
        text = text.replace(accented, plain)
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')


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
        unit_price = effective_price_cents(product)
        subtotal_cents = unit_price * qty
        total_cents += subtotal_cents
        items.append({
            'product': product,
            'quantity': qty,
            'unit_price_cents': unit_price,
            'subtotal_display': price_display(subtotal_cents)
        })
    return items, total_cents


def image_url(product):
    """Las imágenes originales del catálogo viven en static/img (parte del
    código). Las imágenes subidas desde el panel de admin se guardan como
    'uploads/archivo.jpg' y se sirven desde el volumen persistente."""
    image = product['image']
    if image.startswith('uploads/'):
        return url_for('serve_upload', filename=image[len('uploads/'):])
    return url_for('static', filename='img/' + image)


app.jinja_env.globals.update(price_display=price_display, image_url=image_url, has_discount=has_discount, effective_price_cents=effective_price_cents)

# ----------------------------
# CATEGORÍAS DEL CATÁLOGO
# ----------------------------

CATEGORY_ORDER = ['kit_emergencia', 'kit_incendios', 'kit_viajes', 'kit_automovil']
BUSINESS_CATEGORY_ORDER = ['nfc_resenas', 'menu_digital', 'kit_incendio_hospedagem', 'kit_boas_vindas_hospedagem']
ALL_CATEGORIES = CATEGORY_ORDER + BUSINESS_CATEGORY_ORDER

CATEGORY_IMAGES = {
    'kit_emergencia': 'categoria-emergencia.jpg',
    'kit_incendios': 'categoria-incendios.jpg',
    'kit_viajes': 'categoria-viagens.jpg',
    'kit_automovil': 'categoria-automovel.jpg',
    'nfc_resenas': 'categoria-nfc-resenas.jpg',
    'menu_digital': 'categoria-menu-digital.jpg',
    'kit_incendio_hospedagem': 'categoria-incendio-hospedagem.jpg',
    'kit_boas_vindas_hospedagem': 'categoria-boas-vindas-hospedagem.jpg',
}


def category_image_url(cat_slug):
    return url_for('static', filename='img/' + CATEGORY_IMAGES.get(cat_slug, ''))


def is_business_category(cat_slug):
    return cat_slug in BUSINESS_CATEGORY_ORDER


app.jinja_env.globals.update(
    category_order=CATEGORY_ORDER,
    business_category_order=BUSINESS_CATEGORY_ORDER,
    category_image_url=category_image_url,
    is_business_category=is_business_category,
)

SUPPORTED_LANGUAGES = ['pt', 'es', 'en']
LANGUAGE_LABELS = {'pt': 'PT', 'es': 'ES', 'en': 'EN'}
# Códigos de país (ISO 3166-1 alpha-2) usados por la librería flag-icons
# para dibujar la bandera como icono. Se usan en vez de emoji porque
# Windows no renderiza los emoji de bandera (muestra las letras sueltas).
LANGUAGE_FLAGS = {'pt': 'pt', 'es': 'es', 'en': 'gb'}


def get_lang():
    lang = session.get('lang', 'pt')
    return lang if lang in SUPPORTED_LANGUAGES else 'pt'


def t(key):
    return translate(key, get_lang())


app.jinja_env.globals.update(t=t, current_lang=get_lang, supported_languages=SUPPORTED_LANGUAGES, language_labels=LANGUAGE_LABELS, language_flags=LANGUAGE_FLAGS)


@app.route('/idioma/<lang_code>')
def set_language(lang_code):
    if lang_code in SUPPORTED_LANGUAGES:
        session['lang'] = lang_code
    return redirect(request.referrer or url_for('home'))


@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# Contraseña del panel de administración.
# En Railway, configúrala como variable de entorno ADMIN_PASSWORD para no
# dejarla escrita en el código. Localmente, si no la defines, usa esta por
# defecto SOLO para pruebas.
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'teukit2026')

# ----------------------------
# CONFIGURACIÓN DE EMAIL (API de Brevo, vía HTTPS)
# ----------------------------
# Railway bloquea las conexiones SMTP salientes en su plan gratuito, así que
# usamos la API HTTP de Brevo en vez de SMTP — funciona en cualquier plan.
# Configura en Railway: BREVO_API_KEY, SENDER_EMAIL, ADMIN_NOTIFICATION_EMAIL
BREVO_API_KEY = os.environ.get('BREVO_API_KEY')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'naoresponder@teukit.pt')
SENDER_NAME = os.environ.get('SENDER_NAME', 'TeuKit')
ADMIN_NOTIFICATION_EMAIL = os.environ.get('ADMIN_NOTIFICATION_EMAIL')
BREVO_API_URL = 'https://api.brevo.com/v3/smtp/email'

# ----------------------------
# DATOS DE CONTACTO
# ----------------------------
WHATSAPP_NUMBER = '351911900229'  # sin '+' ni espacios, formato requerido por wa.me
INSTAGRAM_URL = 'https://www.instagram.com/teu_kitpt?stkn=MTV4NTNqZm54NDZqeQ=='
CONTACT_EMAIL = 'teukit.pt@gmail.com'

app.jinja_env.globals.update(
    whatsapp_number=WHATSAPP_NUMBER,
    instagram_url=INSTAGRAM_URL,
    contact_email=CONTACT_EMAIL,
)


def send_email(to_email, subject, html_body):
    """Envía un email a través de la API HTTP de Brevo. Si no hay API key
    configurada (ej. en desarrollo local), no falla: solo lo registra en la
    consola y continúa."""
    if not BREVO_API_KEY:
        print(f"[EMAIL NO ENVIADO - falta BREVO_API_KEY] Para: {to_email} | Asunto: {subject}")
        return False

    try:
        response = requests.post(
            BREVO_API_URL,
            headers={
                'accept': 'application/json',
                'api-key': BREVO_API_KEY,
                'content-type': 'application/json',
            },
            json={
                'sender': {'name': SENDER_NAME, 'email': SENDER_EMAIL},
                'to': [{'email': to_email}],
                'subject': subject,
                'htmlContent': html_body,
            },
            timeout=10
        )
        if response.status_code in (200, 201):
            return True
        print(f"[ERROR AL ENVIAR EMAIL] Para: {to_email} | Status: {response.status_code} | {response.text}")
        return False
    except Exception as e:
        print(f"[ERROR AL ENVIAR EMAIL] Para: {to_email} | Error: {e}")
        return False


def send_order_emails(order_id, name, email, address, phone, items, total_cents):
    items_html = "".join(
        f"<li>{item['quantity']}x {item['product']['name']} — {price_display(item['unit_price_cents'] * item['quantity'])}</li>"
        for item in items
    )
    total_str = price_display(total_cents)

    # Email al cliente
    customer_html = f"""
    <div style="font-family: sans-serif; max-width: 500px; margin: 0 auto;">
        <h2 style="color: #0f2d4f;">Obrigado pelo teu pedido, {name}!</h2>
        <p>O teu pedido <strong>#{order_id}</strong> foi registado com sucesso.</p>
        <ul>{items_html}</ul>
        <p><strong>Total: {total_str}</strong></p>
        <p>Morada de envio: {address}</p>
        <p>Entraremos em contacto brevemente para confirmar o pagamento e envio.</p>
        <p style="color: #888; font-size: 0.85rem;">TeuKit — Já tens o teu?</p>
    </div>
    """
    send_email(email, f"Confirmação do teu pedido #{order_id} — TeuKit", customer_html)

    # Email al administrador
    if ADMIN_NOTIFICATION_EMAIL:
        admin_html = f"""
        <div style="font-family: sans-serif; max-width: 500px; margin: 0 auto;">
            <h2 style="color: #d9432e;">🎉 Novo pedido recebido — #{order_id}</h2>
            <p><strong>Cliente:</strong> {name}</p>
            <p><strong>Email:</strong> {email}</p>
            <p><strong>Telefone:</strong> {phone or '—'}</p>
            <p><strong>Morada:</strong> {address}</p>
            <ul>{items_html}</ul>
            <p><strong>Total: {total_str}</strong></p>
            <p>Vê e gere este pedido no <a href="/admin/pedidos">painel de administração</a>.</p>
        </div>
        """
        send_email(ADMIN_NOTIFICATION_EMAIL, f"Novo pedido #{order_id} — {total_str}", admin_html)


def send_status_update_email(order_id, name, email, new_status):
    status_messages = {
        'pagado': {
            'subject': f"Pagamento confirmado — Pedido #{order_id} — TeuKit",
            'heading': "💳 Pagamento confirmado!",
            'body': f"Confirmámos o pagamento do teu pedido <strong>#{order_id}</strong>. Vamos preparar tudo para o envio."
        },
        'enviado': {
            'subject': f"O teu pedido foi enviado — #{order_id} — TeuKit",
            'heading': "📦 O teu pedido foi enviado!",
            'body': f"O teu pedido <strong>#{order_id}</strong> já está a caminho. Obrigado por confiares na TeuKit!"
        },
    }

    info = status_messages.get(new_status)
    if not info:
        return  # no se envía email para "pendiente" u otros estados

    html = f"""
    <div style="font-family: sans-serif; max-width: 500px; margin: 0 auto;">
        <h2 style="color: #0f2d4f;">{info['heading']}</h2>
        <p>Olá {name},</p>
        <p>{info['body']}</p>
        <p style="color: #888; font-size: 0.85rem;">TeuKit — Já tens o teu?</p>
    </div>
    """
    send_email(email, info['subject'], html)


def send_contact_message(name, email, message):
    """Envía la consulta del formulario de contacto al email del negocio."""
    html = f"""
    <div style="font-family: sans-serif; max-width: 500px; margin: 0 auto;">
        <h2 style="color: #0f2d4f;">📩 Nova mensagem de contacto</h2>
        <p><strong>Nome:</strong> {name}</p>
        <p><strong>Email:</strong> {email}</p>
        <p><strong>Mensagem:</strong></p>
        <p style="white-space: pre-line; background: #f4f6f8; padding: 12px; border-radius: 8px;">{message}</p>
    </div>
    """
    send_email(CONTACT_EMAIL, f"Nova mensagem de contacto — {name}", html)


# ----------------------------
# GUÍA EN PDF (generada dinámicamente en el idioma activo)
# ----------------------------

EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]+",
    flags=re.UNICODE
)


def strip_emoji(text):
    return EMOJI_PATTERN.sub('', text).strip()


def generate_guide_pdf(lang):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 2.2 * cm
    max_width = width - 2 * margin
    y = height - margin

    navy = HexColor('#0f2d4f')
    text_color = HexColor('#333333')
    red = HexColor('#d9432e')

    def draw_wrapped(text, x, top_y, font='Helvetica', size=10, leading=14, color=text_color):
        c.setFont(font, size)
        c.setFillColor(color)
        line = ''
        cur_y = top_y
        for word in text.split(' '):
            test = (line + ' ' + word).strip()
            if c.stringWidth(test, font, size) > max_width and line:
                c.drawString(x, cur_y, line)
                cur_y -= leading
                line = word
            else:
                line = test
        if line:
            c.drawString(x, cur_y, line)
            cur_y -= leading
        return cur_y

    def ensure_space(cur_y, needed=70):
        if cur_y < margin + needed:
            c.showPage()
            return height - margin
        return cur_y

    # Encabezado
    c.setFillColor(navy)
    c.setFont('Helvetica-Bold', 22)
    c.drawString(margin, y, "TeuKit")
    y -= 30
    y = draw_wrapped(strip_emoji(translate('guide.title', lang)), margin, y,
                      font='Helvetica-Bold', size=15, leading=19, color=navy)
    y -= 8
    y = draw_wrapped(translate('guide.intro', lang), margin, y, leading=14)
    y -= 14

    for i in range(1, 9):
        title = strip_emoji(translate(f'guide.item{i}_title', lang))
        desc = translate(f'guide.item{i}_desc', lang)
        y = ensure_space(y)
        c.setFillColor(navy)
        c.setFont('Helvetica-Bold', 11.5)
        c.drawString(margin, y, title)
        y -= 16
        y = draw_wrapped(desc, margin, y, leading=13)
        y -= 10

    y = ensure_space(y)
    c.setFillColor(red)
    c.setFont('Helvetica-Bold', 12.5)
    c.drawString(margin, y, strip_emoji(translate('guide.emergency_title', lang)))
    y -= 18
    emergency_body = re.sub('<[^<]+?>', '', translate('guide.emergency_body', lang))
    draw_wrapped(emergency_body, margin, y, leading=14)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer


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


@app.route('/catalogo')
def catalog():
    return render_template('catalog.html')


@app.route('/empresas')
def business_catalog():
    return render_template('business_catalog.html')


@app.route('/categoria/<cat_slug>')
def category_view(cat_slug):
    if cat_slug not in ALL_CATEGORIES:
        return redirect(url_for('catalog'))
    db = get_db()
    products = db.execute(
        'SELECT * FROM product WHERE category = ? AND active = 1', (cat_slug,)
    ).fetchall()
    is_business = is_business_category(cat_slug)
    return render_template(
        'category.html', products=products, cat_slug=cat_slug, is_business=is_business
    )


@app.route('/producto/<slug>')
def product_detail(slug):
    db = get_db()
    product = db.execute('SELECT * FROM product WHERE slug = ? AND active = 1', (slug,)).fetchone()
    if not product:
        return "Producto no encontrado", 404
    is_business = is_business_category(product['category'])
    return render_template('product_detail.html', product=product, is_business=is_business)


@app.route('/guia')
def guide():
    return render_template('guide.html')


@app.route('/guia/pdf')
def guide_pdf():
    lang = get_lang()
    buffer = generate_guide_pdf(lang)
    filename = f'guia-kit-essencial-teukit-{lang}.pdf'
    return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=filename)


@app.route('/privacidade')
def privacy_policy():
    lang = get_lang()
    template = 'privacy.html' if lang == 'pt' else f'privacy_{lang}.html'
    return render_template(template)


@app.route('/termos')
def terms():
    lang = get_lang()
    template = 'terms.html' if lang == 'pt' else f'terms_{lang}.html'
    return render_template(template)


@app.route('/contacto', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        message = request.form.get('message', '').strip()

        if not name or not email or not message:
            flash(t('contact.error_fields'), 'error')
            return render_template('contact.html')

        threading.Thread(
            target=send_contact_message,
            args=(name, email, message),
            daemon=True
        ).start()

        flash(t('contact.success'), 'success')
        return redirect(url_for('contact'))

    return render_template('contact.html')


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
                  item['unit_price_cents'], item['quantity']))

        db.commit()

        # --- INTEGRACIÓN DE PAGO (PLACEHOLDER) ---
        # Aquí es donde se conecta Stripe cuando tengas tu cuenta creada.
        # Ver create_stripe_checkout_session() comentada más abajo.
        # De momento el pedido queda registrado como "pendiente".

        # --- NOTIFICACIONES POR EMAIL (en segundo plano, no bloquea la compra) ---
        threading.Thread(
            target=send_order_emails,
            args=(order_id, name, email, address, phone, items, total_cents),
            daemon=True
        ).start()

        save_cart({})
        return redirect(url_for('order_confirmation', order_id=order_id))

    return render_template('checkout.html', items=items, total_display=price_display(total_cents), current_user=current_user)


@app.route('/pedido/<int:order_id>/confirmacion')
def order_confirmation(order_id):
    db = get_db()
    order = db.execute('SELECT * FROM "order" WHERE id = ?', (order_id,)).fetchone()
    if not order:
        return "Pedido no encontrado", 404
    return render_template('order_confirmation.html', order=order, total_display=price_display(order['total_cents']))


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
    order = db.execute('SELECT * FROM "order" WHERE id = ?', (order_id,)).fetchone()
    if not order:
        flash('Pedido no encontrado.', 'error')
        return redirect(url_for('admin_orders'))

    db.execute('UPDATE "order" SET status = ? WHERE id = ?', (new_status, order_id))
    db.commit()

    threading.Thread(
        target=send_status_update_email,
        args=(order_id, order['customer_name'], order['customer_email'], new_status),
        daemon=True
    ).start()

    flash(f'Pedido #{order_id} actualizado a "{new_status}".', 'success')
    return redirect(url_for('admin_orders'))


@app.route('/admin/produtos')
@admin_required
def admin_products():
    db = get_db()
    products = db.execute('SELECT * FROM product ORDER BY id').fetchall()
    return render_template('admin_products.html', products=products)


def save_uploaded_image(file_storage):
    """Guarda una imagen subida en el volumen persistente y devuelve el
    valor a guardar en la columna 'image' (con el prefijo 'uploads/').
    Devuelve None si no se subió ningún archivo."""
    if not file_storage or file_storage.filename == '':
        return None

    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError('Formato de imagen no permitido. Usa PNG, JPG o WEBP.')

    unique_name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(UPLOAD_FOLDER, unique_name))
    return f"uploads/{unique_name}"


@app.route('/admin/produtos/novo', methods=['GET', 'POST'])
@admin_required
def admin_new_product():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        price_str = request.form.get('price', '').strip()
        description = request.form.get('description', '').strip()
        stock = request.form.get('stock', '').strip()
        category = request.form.get('category', '').strip()
        discount_str = request.form.get('discount_percent', '').strip()

        if not name or not description or not stock.isdigit() or category not in ALL_CATEGORIES:
            flash('Preenche todos os campos corretamente.', 'error')
            return render_template('admin_product_form.html', mode='new')

        try:
            price_cents = round(float(price_str.replace(',', '.')) * 100)
        except ValueError:
            flash('El precio no es válido.', 'error')
            return render_template('admin_product_form.html', mode='new')

        try:
            discount_percent = int(discount_str) if discount_str else 0
            if discount_percent < 0 or discount_percent > 90:
                raise ValueError
        except ValueError:
            flash('El descuento debe ser un número entre 0 y 90.', 'error')
            return render_template('admin_product_form.html', mode='new')

        db = get_db()
        base_slug = slugify(name)
        slug = base_slug
        counter = 2
        while db.execute('SELECT id FROM product WHERE slug = ?', (slug,)).fetchone():
            slug = f"{base_slug}-{counter}"
            counter += 1

        try:
            image_value = save_uploaded_image(request.files.get('image'))
        except ValueError as e:
            flash(str(e), 'error')
            return render_template('admin_product_form.html', mode='new')

        if not image_value:
            flash('Selecciona una imagen para el producto.', 'error')
            return render_template('admin_product_form.html', mode='new')

        db.execute('''
            INSERT INTO product (name, slug, price_cents, description, image, stock, active, category, discount_percent)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        ''', (name, slug, price_cents, description, image_value, int(stock), category, discount_percent))
        db.commit()
        flash(f'Produto "{name}" criado com sucesso.', 'success')
        return redirect(url_for('admin_products'))

    return render_template('admin_product_form.html', mode='new')


@app.route('/admin/produtos/<int:product_id>/editar', methods=['POST'])
@admin_required
def admin_update_product(product_id):
    name = request.form.get('name', '').strip()
    stock = request.form.get('stock', '').strip()
    description = request.form.get('description', '').strip()
    price_str = request.form.get('price', '').strip()
    active = 1 if request.form.get('active') == 'on' else 0
    category = request.form.get('category', '').strip()
    discount_str = request.form.get('discount_percent', '').strip()

    if not name or not stock.isdigit() or not description or category not in ALL_CATEGORIES:
        flash('Preenche todos os campos corretamente.', 'error')
        return redirect(url_for('admin_products'))

    try:
        price_cents = round(float(price_str.replace(',', '.')) * 100)
    except ValueError:
        flash('El precio no es válido.', 'error')
        return redirect(url_for('admin_products'))

    try:
        discount_percent = int(discount_str) if discount_str else 0
        if discount_percent < 0 or discount_percent > 90:
            raise ValueError
    except ValueError:
        flash('El descuento debe ser un número entre 0 y 90.', 'error')
        return redirect(url_for('admin_products'))

    try:
        new_image = save_uploaded_image(request.files.get('image'))
    except ValueError as e:
        flash(str(e), 'error')
        return redirect(url_for('admin_products'))

    db = get_db()
    if new_image:
        db.execute(
            'UPDATE product SET name = ?, stock = ?, description = ?, price_cents = ?, active = ?, image = ?, category = ?, discount_percent = ? WHERE id = ?',
            (name, int(stock), description, price_cents, active, new_image, category, discount_percent, product_id)
        )
    else:
        db.execute(
            'UPDATE product SET name = ?, stock = ?, description = ?, price_cents = ?, active = ?, category = ?, discount_percent = ? WHERE id = ?',
            (name, int(stock), description, price_cents, active, category, discount_percent, product_id)
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
