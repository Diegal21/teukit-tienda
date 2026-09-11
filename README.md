# TeuKit — Tienda online

Web de venta con carrito de compra para TeuKit, construida con Flask.

## Cómo probarlo en tu PC (Windows/Mac)

1. **Abre una terminal dentro de la carpeta del proyecto** (`teukit/`).
   - En VS Code: `Terminal` → `Nueva terminal`, ya te abre en la carpeta correcta.

2. **Crea un entorno virtual** (esto mantiene las librerías de este proyecto separadas del resto de tu PC):
   ```
   python -m venv venv
   ```

3. **Actívalo:**
   - Windows: `venv\Scripts\activate`
   - Mac/Linux: `source venv/bin/activate`

   Sabrás que funcionó porque verás `(venv)` al inicio de la línea de la terminal.

4. **Instala las dependencias:**
   ```
   pip install -r requirements.txt
   ```

5. **Ejecuta la web:**
   ```
   python app.py
   ```

6. **Abre tu navegador** en: [http://localhost:5000](http://localhost:5000)

   Deberías ver la tienda con el KIT Essencial. Prueba a añadirlo al carrito y completar un pedido de prueba.

## Cómo añadir un nuevo producto

Abre `app.py`, busca la función `seed_products()` y copia el bloque del producto `kit`, cambiando nombre, slug (sin espacios ni tildes), precio en céntimos, descripción e imagen. Luego borra el archivo `teukit.db` (se recreará solo) y vuelve a ejecutar `python app.py`.

## Desplegar en Railway

1. Sube esta carpeta a un repositorio de GitHub (puedes arrastrarla directamente en la web de GitHub si aún no usas comandos de Git, o usar `git init`, `git add .`, `git commit -m "primer commit"`, `git push`).
2. En Railway: `New Project` → `Deploy from GitHub repo` → selecciona tu repositorio.
3. Railway detecta el `Procfile` y `requirements.txt` automáticamente y despliega.
4. En **Settings → Networking**, genera un dominio público para acceder a tu web.

## Cuentas de cliente

Los clientes ahora pueden crear una cuenta (`/registro`), iniciar sesión (`/login`) y ver su historial de pedidos en `/minha-conta`. Las contraseñas se guardan cifradas (nunca en texto plano).

El checkout sigue funcionando también sin estar logueado (compra como invitado), pero si el cliente inició sesión, su pedido queda asociado a su cuenta y sus datos se pre-rellenan automáticamente.

## Notificaciones por email

Cuando un cliente completa una compra, se envían automáticamente dos emails:
- Al **cliente**: confirmación con el resumen de su pedido
- A **ti**: aviso de nuevo pedido con los datos para prepararlo

Usamos la **API HTTP de Brevo** (gratis hasta 300 emails/día) — no SMTP, porque Railway bloquea las conexiones SMTP salientes en su plan gratuito. La API funciona sin ese problema, en cualquier plan.

Mientras no configures la API key, el sistema simplemente no envía nada (no rompe la compra) — verás un aviso en los logs.

**Para activarlo:**

1. Crea una cuenta gratis en [brevo.com](https://www.brevo.com/) (si ya la creaste, sáltate este paso)
2. Ve a **Settings → SMTP & API → pestaña "API Keys"** (no la de SMTP) dentro de Brevo
3. Genera una nueva **API Key** y cópiala de inmediato (solo se muestra completa una vez)
4. Verifica también tu email remitente en **Settings → Senders, Domains & Dedicated IPs** si no lo hiciste antes
5. En Railway, añade estas variables de entorno (pestaña **Variables**):

| Variable | Valor |
|---|---|
| `BREVO_API_KEY` | la API Key que acabas de generar |
| `SENDER_EMAIL` | el email remitente verificado en Brevo (ej. `teukit.pt@gmail.com`) |
| `ADMIN_NOTIFICATION_EMAIL` | tu email personal, donde quieres recibir el aviso de nuevos pedidos |

6. Railway redesplegará solo. A partir de ahí, cada compra enviará ambos emails automáticamente.

**Nota:** ya no se usan las variables `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_HOST` ni `SMTP_PORT` — puedes eliminarlas de Railway si las habías configurado antes.

## IMPORTANTE: Volumen persistente en Railway (evita perder datos)

Por defecto, Railway borra el sistema de archivos con cada despliegue — eso significa que **perderías todos los pedidos y cuentas de cliente cada vez que subas un cambio**. Para evitarlo:

1. En Railway, dentro de tu proyecto, haz clic en tu servicio `teukit-tienda`
2. Ve a la pestaña **Settings** → busca la sección **Volumes**
3. Haz clic en **"+ New Volume"**
4. Como **Mount path**, escribe: `/data`
5. Guarda

6. Ahora ve a la pestaña **Variables** y añade una nueva:
   - Nombre: `DB_PATH`
   - Valor: `/data/teukit.db`

7. Railway redesplegará automáticamente. A partir de ahora, tu base de datos vive en ese volumen y **sobrevive** a futuros despliegues.

⚠️ Si ya tenías pedidos de prueba antes de configurar esto, se perderán una vez apliques el volumen (porque cambia de dónde lee la base de datos). Es buen momento para "empezar de cero" con datos reales.

## Gestión de stock y productos

En `/admin/produtos` (dentro del panel de administración) puedes editar por cada producto:
- **Stock disponible** — cuando llega a 0, el producto se muestra como "Esgotado" en la tienda y no se puede comprar
- **Precio**
- **Descripción / contenido del kit**

Los cambios se aplican al instante en la tienda pública.

## Panel de pedidos (administración)

Para ver los pedidos que van llegando y marcarlos como enviados:

1. Entra a `/admin/pedidos` (ej. `http://localhost:5000/admin/pedidos` o `https://tu-dominio-railway.app/admin/pedidos`)
2. Te pedirá una contraseña. Por defecto, en local, es: `teukit2026`
3. Ahí verás cada pedido con los datos del cliente, qué compró, el total, y un menú desplegable para cambiar el estado (pendiente / pago / enviado)

**Importante — cambia la contraseña antes de usarlo en producción:**
En Railway, ve a tu proyecto → pestaña **Variables** → añade una nueva variable:
- Nombre: `ADMIN_PASSWORD`
- Valor: la contraseña que tú quieras (algo fuerte, no compartida con nadie)

Railway reiniciará la app sola con la nueva contraseña activa.

## Pagos con Stripe (pendiente de activar)

El checkout ya está preparado. Cuando crees tu cuenta en Stripe:
1. `pip install stripe` (y añádelo a `requirements.txt`)
2. Añade tu clave secreta como variable de entorno `STRIPE_SECRET_KEY` en Railway
3. Descomenta el bloque de Stripe al final de `app.py` y conéctalo en la ruta `/checkout`

Hasta entonces, los pedidos se registran igualmente en la base de datos con estado "pendiente" para que los gestiones manualmente.
