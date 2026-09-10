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

## Pagos con Stripe (pendiente de activar)

El checkout ya está preparado. Cuando crees tu cuenta en Stripe:
1. `pip install stripe` (y añádelo a `requirements.txt`)
2. Añade tu clave secreta como variable de entorno `STRIPE_SECRET_KEY` en Railway
3. Descomenta el bloque de Stripe al final de `app.py` y conéctalo en la ruta `/checkout`

Hasta entonces, los pedidos se registran igualmente en la base de datos con estado "pendiente" para que los gestiones manualmente.
