# ---------- Worker Persistente de Cotización Positiva ----------
import os
import sys
import io
import json
import re
import time
import signal
import logging
import threading
import subprocess
import redis

from datetime import datetime
from pprint import pformat
from playwright.sync_api import sync_playwright, Playwright, Error as PlaywrightError
from Carpeta.rutas import crear_carpeta_descargas, renombrar_carpeta
from Apis.post import enviar_x_wsp
from Apis.put import enviar_documento
from Chrome.page import tomar_captura

# Forzar salida estándar en UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Configuración del Logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# ⚙️ Variables de Entorno y Configuración del Worker
WORKER_ID = os.getenv("WORKER_ID")
QUEUE_NAME = os.getenv("QUEUE_NAME")
REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT"))
PUERTO = os.getenv("puerto")
entorno = os.getenv("entorno","false").strip().lower() == "true"
BROWSER_DATA_DIR = os.getenv("BROWSER_DATA_DIR","/app/browser_data")
USER_POS = os.getenv("user_pos_cot")
PASS_POS = os.getenv("pass_pos_cot")
URL_COT_POS = os.getenv("url_cot_positiva")
URL_HOST_BASE = os.getenv("url_host_prod",os.getenv("url_n8n_base"))
PASS_EN_GRAFICO = os.getenv("pass_enGrafico", "")

# Estado global del Worker
current_status = "STARTING"
current_job_id = ""
jobs_processed_count = 0
stop_requested = threading.Event()
status_lock = threading.Lock()

# ------------------ HELPERS NORMALIZACIÓN Y MODELOS ------------------

def to_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "si")
    if isinstance(value, (int, float)):
        return value != 0
    return False

def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default

def normalizar_data(data: dict):
    if not isinstance(data, dict):
        return {}
    data = data.copy()
    data["GAS"] = to_bool(data.get("GAS"))
    data["SOAT"] = to_bool(data.get("SOAT"))
    data["INSPECCION"] = to_bool(data.get("INSPECCION"))
    data["CLIENTE_NUEVO"] = to_bool(data.get("CLIENTE_NUEVO"))
    data["ASIENTOS"] = safe_int(data.get("ASIENTOS"))
    data["PRECIO"] = safe_int(data.get("PRECIO"))
    return data

class BaseModel:
    def to_dict(self, ocultar=None):
        data = self.__dict__.copy()
        if ocultar:
            for campo in ocultar:
                if campo in data:
                    data[campo] = "********"
        return data

class Vehiculo(BaseModel):
    def __init__(self, data: dict):
        self.plan = data.get("plan")
        self.num_rodaje = data.get("num_rodaje")
        self.num_motor = data.get("num_motor")
        self.num_serie = data.get("num_serie")
        self.modelo = data.get("modelo")
        self.tipo = data.get("tipo")
        self.clase = data.get("clase")
        self.marca = data.get("marca")
        self.anio = safe_int(data.get("año") or data.get("ao"))
        self.valor = data.get("precio")
        self.uso = data.get("uso")
        self.gas = data.get("gas")
        self.ocupantes = data.get("asientos")
        self.seguro = data.get("soat")
        self.inspeccion = data.get("inspeccion")
        self.localizacion = data.get("localizacion")
        self.distrito = data.get("distrito")

    def __str__(self):
        return f"{str(self.modelo).upper()}|{str(self.marca).upper()}|{self.tipo}|{self.clase}"

class Asesor(BaseModel):
    def __init__(self, data: dict):
        self.nombre = data.get("asesor")
        self.correo = data.get("correo_asesor")

class Ejecutivo(BaseModel):
    def __init__(self, data: dict):
        self.nombre = data.get("ejecutivo")
        self.celular = data.get("celular_ejecutivo")

class Compania(BaseModel):
    def __init__(self, data: dict):
        self.usuario = data.get("usuario")
        self.contrasena = data.get("password")

class Organizacion(BaseModel):
    def __init__(self, data: dict):
        self.nombre = data.get("nom_organizacion")
        self.sede = data.get("sede")
        self.rol = "CANAL NO TRADICIONAL"
        self.canal = data.get("canal")

class Credito(BaseModel):
    def __init__(self, data: dict):
        self.tiempo = data.get("tiempo_credito")
        self.cuotas = safe_int(data.get("cuotas") or data.get("numero_de_cuotas"))
        self.numero_de_cuotas = safe_int(data.get("numero_de_cuotas") or data.get("cuotas"))
        self.forma_pago = data.get("forma_pago")

class Cliente(BaseModel):
    def __init__(self, data: dict):
        self.cliente_nuevo = data.get("cliente_nuevo")
        self.rz_social = data.get("razonsocial")
        self.nombres = data.get("nombres")
        self.apellido_paterno = data.get("paterno")
        self.apellido_materno = data.get("materno")
        self.tipo_persona = data.get("tipo_cliente")
        self.tipo_doc = data.get("tipo_doc")
        self.num_doc = data.get("num_doc")
        fecha = data.get("fecha_nac")
        try:
            self.fecha_nac = datetime.strptime(fecha, "%d-%m-%Y").strftime("%d/%m/%Y") if fecha else None
        except Exception:
            self.fecha_nac = fecha
        self.sexo = data.get("sexo")
        self.estado_civil = data.get("estado_civil")
        self.celular = data.get("celular_cliente")
        self.correo = data.get("correo_cliente")
        self.tipo_via = data.get("tip_via")
        self.nom_via = data.get("nom_via")
        self.num_via = data.get("num_via")

class CotizacionContexto:
    def __init__(self, data: dict):

        self.movimiento = data.get("movimiento") or "COTIZACION"
        self.id_cot = str(data.get("id") or data.get("id_cot") or "0")
        self.compania = Compania(data)
        self.organizacion = Organizacion(data)
        self.vehiculo = Vehiculo(data)
        self.credito = Credito(data)
        self.cliente = Cliente(data)
        self.asesor = Asesor(data)
        self.ejecutivo = Ejecutivo(data)

    def __str__(self):
        return pformat({
            "Compania": self.compania.to_dict(ocultar=["usuario", "contrasena"]),
            "Organizacion": self.organizacion.to_dict(),
            "Vehículo": self.vehiculo.to_dict(ocultar=["num_rodaje", "num_motor", "num_serie"]),
            "Crédito": self.credito.to_dict(),
            "Cliente": self.cliente.to_dict(ocultar=["num_doc", "celular", "correo", "rz_social"]),
            "Asesor": self.asesor.to_dict(),
            "Ejecutivo": self.ejecutivo.to_dict(ocultar=["dni", "celular"])
        })

# ------------------ ESTADOS Y HEARTBEAT REDIS ------------------

def set_worker_status(r_conn, status, job_id=""):
    global current_status, current_job_id
    with status_lock:
        current_status = status
        current_job_id = job_id or ""
        try:
            r_conn.set(f"worker:{WORKER_ID}:status", status)
            r_conn.set(f"worker:{WORKER_ID}:heartbeat", "alive", ex=30)
            info = {
                "worker_id": WORKER_ID,
                "status": status,
                "current_job": current_job_id,
                "puerto": str(PUERTO),
                "queue": QUEUE_NAME,
                "jobs_processed": str(jobs_processed_count),
                "last_heartbeat": datetime.now().isoformat()
            }
            r_conn.hset(f"worker:{WORKER_ID}:info", mapping=info)
            logging.info(f"🟡 Worker {WORKER_ID} estado -> {status}" if status != "READY" else f"🟢 Worker {WORKER_ID} READY")
        except Exception as e:
            logging.warning(f"⚠️ No se pudo actualizar estado en Redis: {e}")

def heartbeat_thread_func(r_conn):
    logging.info(f"❤️ Hilo de Heartbeat iniciado para {WORKER_ID}")
    while not stop_requested.is_set():
        try:
            with status_lock:
                st = current_status
                cj = current_job_id
            r_conn.set(f"worker:{WORKER_ID}:heartbeat", "alive", ex=30)
            r_conn.hset(f"worker:{WORKER_ID}:info", mapping={
                "status": st,
                "current_job": cj,
                "last_heartbeat": datetime.now().isoformat(),
                "jobs_processed": str(jobs_processed_count)
            })
        except Exception as e:
            logging.warning(f"⚠️ Error actualizando heartbeat en Redis: {e}")
        
        # Esperar 10 segundos o hasta que se pida parar
        stop_requested.wait(10)
    logging.info(f"❤️ Hilo de Heartbeat finalizado para {WORKER_ID}")

# ------------------ CONTROL DE INTERACCIÓN HUMANA (x11vnc) ------------------

def bloquear_interaccion():
    try:
        subprocess.run(["x11vnc", "-remote", "viewonly"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logging.info("🔒 Interacción humana bloqueada")
    except Exception as e:
        logging.warning(f"⚠️ Error bloqueando interacción: {e}")

def desbloquear_interaccion():
    try:
        subprocess.run(["x11vnc", "-remote", "noviewonly"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logging.info("✋ Interacción humana habilitada")
    except Exception as e:
        logging.warning(f"⚠️ Error desbloqueando interacción: {e}")

# ------------------ GESTIÓN DEL NAVEGADOR Y SESIÓN ------------------

def iniciar_navegador(playwright: Playwright):
    logging.info("🌐 Iniciando navegador Chromium persistente...")
    os.makedirs(BROWSER_DATA_DIR, exist_ok=True)

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=BROWSER_DATA_DIR,
        headless=False,
        slow_mo=300,
        args=[
            "--start-maximized",
            "--window-size=1920,1080",
            "--window-position=0,0",
            "--no-sandbox",
            "--disable-dev-shm-usage"
        ],
        no_viewport=True,
        accept_downloads=True,
        locale="es-PE",
        timezone_id="America/Lima",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    )

    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['es-PE', 'es']
        });
        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32'
        });
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => 8
        });
    """)

    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(15000)
    page.set_default_navigation_timeout(45000)

    # Por defecto bloquear interacción humana
    bloquear_interaccion()

    logging.info("🌐 Navegador listo y contexto inicializado.")
    return context, page

def sesion_caducada_detectada(page):
    """Detecta si la página actual muestra el mensaje de sesión caducada o login."""
    try:
        if page.get_by_text("Su sesión caducó", exact=False).is_visible(timeout=1000):
            return True
        if page.get_by_text("Por favor, inicie sesión nuevamente", exact=False).is_visible(timeout=1000):
            return True
        if page.get_by_role("textbox", name="Usuario * Usuario *").is_visible(timeout=1000):
            return True
    except Exception:
        pass
    return False

def inicializar_sesion(page, r_conn, ctx=None):
    logging.info("🔐 Inicializando/Restableciendo sesión en POSITIVA...")
    bloquear_interaccion()

    # Si estamos en la pantalla de sesión caducada, hacer clic en el botón 'Ingresar'
    try:
        if page.get_by_text("Su sesión caducó", exact=False).is_visible(timeout=1500) or \
           page.get_by_text("Por favor, inicie sesión nuevamente", exact=False).is_visible(timeout=1500):
            logging.warning("⚠️ Pantalla 'Su sesión caducó' activa. Haciendo clic en 'Ingresar'...")
            btn_ingresar = page.get_by_role("button", name="Ingresar").or_(
                page.locator("button:has-text('Ingresar'), a:has-text('Ingresar'), input[value='Ingresar']")
            )
            if btn_ingresar.first.is_visible(timeout=2000):
                btn_ingresar.first.click()
                time.sleep(1)
            else:
                page.goto(URL_COT_POS, wait_until="networkidle")
        elif not page.get_by_role("textbox", name="Usuario * Usuario *").is_visible(timeout=1000):
            boton_cotizar = page.get_by_role("button", name="Nueva cotización")
            if not boton_cotizar.is_visible(timeout=1500):
                page.goto(URL_COT_POS, wait_until="networkidle")
    except Exception as e:
        logging.warning(f"⚠️ Aviso al preparar navegación/login: {e}")
        try:
            page.goto(URL_COT_POS, wait_until="networkidle")
        except Exception:
            pass

    # Verificar si ya estamos logueados (el botón "Nueva cotización" ya existe)
    boton_cotizar = page.get_by_role("button", name="Nueva cotización")
    try:
        if boton_cotizar.is_visible(timeout=2000):
            bloquear_interaccion()
            logging.info("🟢 Sesión previa válida detectada. No se requiere login.")
            return True
    except Exception:
        pass

    # Bucle de hasta 3 intentos para resolución de CAPTCHA y Login
    MAX_INTENTOS_CAPTCHA = 3
    TIEMPO_ESPERA_INTENTO = int(os.getenv("TIEMPO_ESPERA_CAPTCHA", "180"))  # segundos por intento
    login_exitoso = False

    for intento in range(1, MAX_INTENTOS_CAPTCHA + 1):
        # Si en intentos posteriores ya se resolvió y cargó la pantalla principal
        if intento > 1:
            try:
                boton_cotizar = page.get_by_role("button", name="Nueva cotización")
                if boton_cotizar.is_visible(timeout=1000):
                    login_exitoso = True
                    break
            except Exception:
                pass

        # 1. Ingresar credenciales si los campos están disponibles
        try:
            usuario_input = page.get_by_role("textbox", name="Usuario * Usuario *")
            if usuario_input.is_visible(timeout=3000):
                if not usuario_input.input_value():
                    logging.info("🔑 Ingresando usuario...")
                    usuario_input.click()
                    usuario_input.fill(USER_POS)

                pass_input = page.get_by_role("textbox", name="Contraseña *")
                if pass_input.is_visible(timeout=1000) and not pass_input.input_value():
                    logging.info("🔑 Ingresando contraseña...")
                    pass_input.click()
                    pass_input.fill(PASS_POS)

                # ✋ Habilitar interacción para resolver CAPTCHA manualmente
                desbloquear_interaccion()
                logging.info(f"🧩 CAPTCHA detectado - requiere intervención manual vía noVNC (Intento {intento}/{MAX_INTENTOS_CAPTCHA})")

                if entorno:
                    id_cot_txt = f" N° {ctx.id_cot}" if ctx and getattr(ctx, "id_cot", None) else ""
                    url_vnc = f"{URL_HOST_BASE}:{PUERTO}/vnc_auto.html?password={PASS_EN_GRAFICO}"
                    aviso_intento = f"\n⚠️ Intento {intento} de {MAX_INTENTOS_CAPTCHA}" if intento > 1 else ""
                    mensaje = f"""Ingresar a {url_vnc} y resolver el captcha para continuar con la cotización{id_cot_txt}.{aviso_intento}\n👤 Usuario: {USER_POS}\n🔑 Password: {PASS_POS}"""
                    enviar_x_wsp(tipo="notificacion", mensaje=mensaje)

        except Exception as e:
            logging.info(f"ℹ️ Verificación de campos de login (intento {intento}): {e}")

        # 2. Esperar hasta que se complete el login y aparezca "Nueva cotización"
        logging.info(f"⏳ Esperando que la sesión esté lista (Botón 'Nueva cotización') - Intento {intento}/{MAX_INTENTOS_CAPTCHA} (máx {TIEMPO_ESPERA_INTENTO}s)...")
        try:
            boton_cotizar = page.get_by_role("button", name="Nueva cotización")
            boton_cotizar.wait_for(state="visible", timeout=TIEMPO_ESPERA_INTENTO * 1000)
            login_exitoso = True
            break
        except Exception:
            logging.warning(f"⚠️ Tiempo de espera agotado para el intento {intento}/{MAX_INTENTOS_CAPTCHA} de resolución de CAPTCHA.")

    # 🔒 Bloquear interacción una vez terminado el proceso
    bloquear_interaccion()

    if login_exitoso:
        logging.info("🟢 Login exitoso. Sesión iniciada correctamente.")
        return True
    else:
        logging.error(f"❌ No se resolvió el CAPTCHA tras {MAX_INTENTOS_CAPTCHA} intentos.")
        try:
            logging.info("🔄 Restableciendo a la página principal de login (URL_COT_POS)...")
            page.goto(URL_COT_POS, wait_until="networkidle")
        except Exception as e:
            logging.warning(f"⚠️ Error al regresar a la página de login: {e}")
        return False

def asegurar_sesion_activa(page, r_conn, ctx=None):
    """Verifica que la sesión en POSITIVA siga activa. Si caducó, ejecuta el login y la notificación por WSP."""
    logging.info("🔍 Verificando estado activo de la sesión...")
    
    # 1. Si detectamos la pantalla de 'Su sesión caducó' o formulario de login
    if sesion_caducada_detectada(page):
        logging.warning("⚠️ Sesión expirada detectada. Re-autenticando...")
        return inicializar_sesion(page, r_conn, ctx=ctx)

    # 2. Si el botón 'Nueva cotización' está visible en pantalla
    boton_cotizar = page.get_by_role("button", name="Nueva cotización")
    try:
        if boton_cotizar.is_visible(timeout=1500):
            logging.info("🟢 Sesión activa (Pantalla principal lista).")
            return True
    except Exception:
        pass

    # 3. Si ya estamos dentro del formulario de cotización
    try:
        if page.locator("#numero-de-poliza_input").is_visible(timeout=1000) or \
           page.get_by_text("CorporativosCuzcoIcaMirafloresPremium/EmpresarialPuno Oficina *").is_visible(timeout=1000):
            logging.info("🟢 Sesión activa (Dentro del formulario de cotización).")
            return True
    except Exception:
        pass

    # 4. En caso de duda, revalidar sesión
    logging.warning("⚠️ Estado de sesión no verificado. Revalidando sesión...")
    return inicializar_sesion(page, r_conn, ctx=ctx)

def verificar_modal_error(page):
    """Verifica si el portal mostró un modal de error ('Vuelva a intentarlo' u otros modales de bloqueo)."""
    try:
        modal = page.locator("#modales")
        if modal.is_visible(timeout=1000):
            texto_modal = modal.inner_text().strip()
            if texto_modal:
                logging.warning(f"⚠️ Modal de alerta detectado: {texto_modal}")
                btn_aceptar = modal.get_by_role("button", name="Aceptar")
                if btn_aceptar.is_visible(timeout=1000):
                    btn_aceptar.click()
                raise Exception(f"Error en portal de Positiva: {texto_modal.replace(chr(10), ' ')}")
    except Exception as e:
        if "Error en portal de Positiva" in str(e):
            raise

def reset_session(page, r_conn):
    """Devuelve la página a un estado conocido sin cerrar el navegador."""
    logging.info("🔄 Verificando y restableciendo estado de la sesión...")
    bloquear_interaccion()
    try:
        # Si hay un modal o diálogo abierto, intentar cerrarlo primero
        try:
            btn_modal = page.locator("#modales button").first
            if btn_modal.is_visible(timeout=1000):
                btn_modal.click()
                time.sleep(0.5)
        except Exception:
            pass

        # Si el botón 'Nueva cotización' ya está visible en pantalla
        boton_cotizar = page.get_by_role("button", name="Nueva cotización")
        if boton_cotizar.is_visible(timeout=1500):
            logging.info("🟢 Sesión lista en la pantalla principal.")
            return True

        # Si no está visible o la sesión caducó, navegar al home / login
        page.goto(URL_COT_POS, wait_until="networkidle")

        if boton_cotizar.is_visible(timeout=3000):
            logging.info("🟢 Sesión restablecida en la pantalla principal.")
            return True

        # Si tras navegar no está el botón, el navegador queda listo en la página de login
        logging.info("ℹ️ Navegador posicionado en la página de login a la espera del próximo Job.")
        return True

    except Exception as e:
        logging.warning(f"⚠️ Error al intentar restablecer sesión: {e}")
        try:
            page.goto(URL_COT_POS, wait_until="networkidle")
        except Exception:
            pass
    return False

# ------------------ PROCESAMIENTO DE JOBS ------------------

def procesar_job(page, raw_payload, job_id, r_conn):

    bloquear_interaccion()
    logging.info(f"⚙️ Procesando Job {job_id}...")
    
    data = normalizar_data(raw_payload)
    ctx = CotizacionContexto(data)

    ruta_carpeta = crear_carpeta_descargas(ctx,entorno)

    if not entorno:
        logging.info(ctx)

    cotizacion = False
    msj_error = None
    error = False

    try:
        # Asegurar sesión activa antes de iniciar el Job
        if not asegurar_sesion_activa(page, r_conn, ctx=ctx):
            raise Exception("No se pudo iniciar sesión en Positiva: CAPTCHA no resuelto tras 3 intentos")

        # Asegurarse de estar en el formulario de cotización
        boton_nueva = page.get_by_role("button", name="Nueva cotización")
        if boton_nueva.is_visible(timeout=3000):
            boton_nueva.click()
            logging.info("🖱️ Clic en 'Nueva cotización'")

        oficina_selector = page.get_by_text("CorporativosCuzcoIcaMirafloresPremium/EmpresarialPuno Oficina *")
        if not oficina_selector.is_visible(timeout=5000) and sesion_caducada_detectada(page):
            logging.warning("⚠️ Sesión expiró al abrir 'Nueva cotización'. Re-inicializando...")
            if not asegurar_sesion_activa(page, r_conn, ctx=ctx):
                raise Exception("No se pudo iniciar sesión en Positiva: CAPTCHA no resuelto tras 3 intentos")
            if boton_nueva.is_visible(timeout=3000):
                boton_nueva.click()
                logging.info("🖱️ Clic en 'Nueva cotización' (reintento)")

        oficina_selector.wait_for(state="visible", timeout=20000)
        oficina_selector.click()
        page.locator("span").filter(has_text="Premium/Empresarial").click()
        logging.info("🖱️ Seleccionando tipo de cotización Premium/Empresarial")

        # Seleccionar Corporativos con manejo de posibles modales de error/reintento
        for intento in range(2):
            page.locator(".ui-input.ui-radio.ui-radio-product-corporativo > .ui-radio-check-icon").click()
            logging.info("🖱️ Seleccionando tipo de producto : Corporativos")
            time.sleep(0.5)

            # Verificar si saltó el modal de 'Vuelva a intentarlo'
            modal_error = page.locator("#modales").get_by_text("Vuelva a intentarlo")
            if modal_error.is_visible(timeout=1500):
                logging.warning(f"⚠️ Modal 'Vuelva a intentarlo' detectado (intento {intento + 1}/2). Cerrando modal...")
                btn_aceptar = page.locator("#modales").get_by_role("button", name="Aceptar")
                if btn_aceptar.is_visible(timeout=1500):
                    btn_aceptar.click()
                time.sleep(1)
                if intento == 1:
                    raise Exception("El portal de Positiva mostró error 'Vuelva a intentarlo' persistente.")
                continue
            break

        # Esperar a que loader termine si está presente
        loader = page.locator(".ui-loader-active")
        if loader.is_visible(timeout=1000):
            loader.wait_for(state="hidden", timeout=10000)

        page.locator("#numero-de-poliza_input").click()
        verificar_modal_error(page)

        try:
            logging.info("🔎 Buscando documento 230286939")

            resultado = page.locator("span").filter(
                has_text="230286939"
            ).first

            resultado.wait_for(state="visible", timeout=15000)
            resultado.click(timeout=5000)

            logging.info("✅ Documento 230286939 seleccionado")

        except Exception as e:
            logging.warning(f"{e}")
            raise Exception("No se encontró la póliza de positiva")

        #page.locator("span").filter(has_text="230286939").first.click()
        #logging.info("🖱️ Seleccionando póliza de positiva")

        page.locator("#contratante_read").click()
        page.locator("#contratante_read").fill("")
        page.locator("#contratante_read").press_sequentially(str(ctx.cliente.num_doc), delay=100)
        logging.info(f"⌨️ Ingresando número de documento: {ctx.cliente.num_doc}")

        # Esperar resultados en la lista desplegable (.ui-dropdown-list)
        encontrado = False
        inicio = time.time()
        while time.time() - inicio < 30:
            opcion_cliente = page.locator(".ui-dropdown-list li").filter(has_text=str(ctx.cliente.num_doc))
            if opcion_cliente.count() > 0 and opcion_cliente.first.is_visible():
                opcion_cliente.first.click()
                logging.info(f"👤 Contratante existente seleccionado: {ctx.cliente.num_doc}")
                encontrado = True
                break
            time.sleep(0.3)

        if not encontrado:
            logging.info("➕ Contratante no encontrado en la lista. Registrando nuevo contratante...")
            btn_nuevo = page.locator(".ui-label-register, a:has-text('Registrar nuevo contratante')").first
            btn_nuevo.click()
            logging.info("🖱️ Clic en registrar nuevo contratante")

            page.locator("#ddTipoDocumento0_input").click()
            page.locator("span").filter(has_text=f"{ctx.cliente.tipo_doc}").first.click()
            logging.info(f"🖱️ Clic en {ctx.cliente.tipo_doc}")

            page.get_by_role("textbox", name="Número de documento *").click()
            page.get_by_role("textbox", name="Número de documento *").fill(ctx.cliente.num_doc)
            logging.info(f"📝 Ingresando numero de documento {ctx.cliente.num_doc}")

            page.locator(".natural-person-item > .columns-2 > .column-main").click()
            logging.info("🖱️ Clic fuera")

            page.get_by_role("textbox", name="Correo electrónico").click()
            page.get_by_role("textbox", name="Correo electrónico").press("CapsLock")
            page.get_by_role("textbox", name="Correo electrónico").fill(ctx.cliente.correo or "")
            logging.info(f"⌨️ Ingresando correo electrónico: {ctx.cliente.correo}")

            page.get_by_role("textbox", name="Celular Celular").click()
            page.get_by_role("textbox", name="Celular Celular").fill(str(ctx.cliente.celular or ""))
            logging.info(f"⌨️ Ingresando numero celular: {ctx.cliente.celular}")

            page.get_by_text("Continuar").first.click()
            logging.info("🖱️ Clic en 'Continuar'")

            page.locator("#create-and-copy-home-address-btn").get_by_text("Crear").click()
            logging.info("🖱️ Clic en 'Crear'")

        else:

            logging.info("🔍 Verificando correo electrónico...")
            campo_correo = page.get_by_role("textbox", name=re.compile(r"Correo Electr[oó]nico", re.I)).first
            try:
                campo_correo.wait_for(state="visible", timeout=5000)
                correo_actual = campo_correo.input_value().strip()
                if not correo_actual:
                    logging.info(f"⌨️ Correo vacío, ingresando: {ctx.cliente.correo}")
                    campo_correo.click()
                    campo_correo.fill(ctx.cliente.correo or "")
                else:
                    logging.info(f"ℹ️ Correo ya existente: '{correo_actual}'")
            except Exception as e:
                logging.warning(f"⚠️ No se pudo validar o ingresar el correo del contratante existente: {e}")
                raise Exception("No se pudo ingresar el correo del cliente")

        page.locator("#uso_input").click()
        logging.info("🖱️ Clic en 'Uso'")
        page.locator("span").filter(has_text=f"{str(ctx.vehiculo.uso).capitalize()}").first.click()
        logging.info(f"🖱️ Seleccionando '{str(ctx.vehiculo.uso).capitalize()}'")

        page.locator("#producto_input").click()
        logging.info("🖱️ Clic en 'Producto'")

        uso = "TOTAL" if str(ctx.vehiculo.uso).upper() == "PARTICULAR" else "COMERCIAL"
        ubicacion = str(ctx.vehiculo.localizacion).upper()

        text = f"US$ AUTO {uso} - DONGFENG - DOLARES - {'LIMA' if ubicacion == 'LIMA' else 'PROVINCIAS'}"

        page.locator("span").filter(has_text=text).first.click()
        logging.info(f"⌨️ Seleccionando '{text}'")

        page.locator("#placa_en_tramite_check").click()
        logging.info("🖱️ Clic en placa en tramite")

        page.locator("#chasis_read").click()
        page.locator("#chasis_read").fill("CHASISDEPRUEBAABC")
        page.locator("#chasis_read").press("Enter")
        logging.info("⌨️ Ingresando chasis de prueba")

        page.locator("#anio-fabricacion_input").click()
        logging.info("🖱️ Clic en año de fabricación")
        page.locator("#anio-fabricacion_content li, #anio-fabricacion_content span[data-bind*='label']").filter(has_text=re.compile(f"^{re.escape(str(ctx.vehiculo.anio))}$")).first.click()
        logging.info(f"⌨️ Seleccionando año de fabricación {ctx.vehiculo.anio}")

        # CLASE
        try:
            logging.info("🖱️ Seleccionando clase del vehículo")

            page.locator("#clase_input").click()

            def seleccionar_clase(texto):
                try:
                    opcion = page.locator("li").filter(
                        has_text=re.compile(
                            f"^{re.escape(texto)}$",
                            re.IGNORECASE
                        )
                    ).first

                    opcion.click(timeout=5000)

                except Exception:
                    raise Exception(f"No existe la clase '{texto}'")

            if str(ctx.vehiculo.clase).upper() == "AUTOMOVIL":
                seleccionar_clase("Automovil")

            else:
                match str(ctx.vehiculo.tipo).upper():

                    case "PICK UP 4X2":
                        seleccionar_clase("Cmta. Pick Up/Cabina Simple")

                    case "PICK UP 4X4":
                        seleccionar_clase("Cmta. Pick Up/Doble Cabina")

                    case "RURAL":
                        if safe_int(ctx.vehiculo.ocupantes) > 9:
                            seleccionar_clase("Camioneta rural mayor 9 astos")
                        else:
                            seleccionar_clase("Camioneta Rural hasta 9 Astos")

                    case _:
                        raise Exception(
                            f"Tipo de vehículo no contemplado: "
                            f"'{ctx.vehiculo.tipo}'"
                        )

            logging.info("🖱️ Clase del vehículo seleccionada")

        except Exception as e:
            logging.warning(f"{e}")
            raise Exception(f"No se pudo seleccionar la clase del vehículo")

        def seleccionar_dropdown(pg, input_selector, texto):
            pg.locator(input_selector).click()
            time.sleep(0.3)
            patron = re.compile(rf"^\s*{re.escape(str(texto))}\s*$", re.IGNORECASE)
            opcion = pg.locator("li, span[data-bind*='label']").filter(has_text=patron)

            if opcion.count() == 0:
                raise Exception(f"No existe la opción '{texto}'")

            opcion.first.click(timeout=5000)

            # if opcion.count() == 0:
            #     opcion = pg.locator("li, span[data-bind*='label']").filter(has_text=str(texto))
            # opcion.first.click()

        def normalizar_texto(texto):
            return re.sub(r"\s+", "", str(texto)).upper()

        # MARCA
        try:
            logging.info("🖱️ Seleccionando marca")
            marca = normalizar_texto(ctx.vehiculo.marca)
            page.locator("#marca_input").click()
            page.locator("li").filter(has_text=re.compile(f"^{re.escape(marca)}$", re.IGNORECASE)).first.click()
            logging.info(f"🖱️ Marca seleccionada: {marca}")
        except Exception as e:
            logging.warning(f"{e}")
            raise Exception(f"No existe la marca '{marca}'")

        # MODELO
        try:
            modelo = str(ctx.vehiculo.modelo).strip()
            seleccionar_dropdown(page, "#modelo_input", modelo)
            logging.info(f"🖱️ Modelo seleccionado: {modelo}")
        except Exception as e:
            logging.warning(f"{e}")
            raise Exception(f"No existe el modelo '{ctx.vehiculo.modelo}'")

        # VERSIÓN
        version = "SIN VERSIÓN"
        seleccionar_dropdown(page, "#version_input", version)
        logging.info(f"🖱️ Versión seleccionada: {version}")

        # ENDOSADO
        endosado = "No"
        seleccionar_dropdown(page, "#endosado_input", endosado)
        logging.info(f"🖱️ Endosado seleccionado: {endosado}")

        # SUMA ASEGURADA
        suma_asegurada = str(ctx.vehiculo.valor).strip()
        page.get_by_role("textbox", name="Suma asegurada *").fill(suma_asegurada)
        logging.info(f"⌨️ Suma asegurada ingresada: {suma_asegurada}")

        # SIMULAR
        page.get_by_text("Simular", exact=True).first.click()
        logging.info("🖱️ Clic en 'Simular'")

        # FORMA DE PAGO
        forma_pago = str(ctx.credito.forma_pago).strip().upper()
        seleccionar_dropdown(page, "#tipo-pago_input", "Al Contado" if forma_pago == "AL CONTADO" else "En Cupones")
        logging.info(f"📌 Forma de pago: {forma_pago}")

        # PAGO EN CUPONES
        if forma_pago != "AL CONTADO":
            logging.info("⌨️ Configurando pago en cupones")
            time.sleep(0.5)
            loader = page.locator(".ui-loader-active")
            if loader.is_visible(timeout=1000):
                loader.wait_for(state="hidden", timeout=10000)

            seleccionar_dropdown(page, "#condiciones_input", "943751 - Afiliado al Débito")
            logging.info("🖱️ Condición seleccionada: 943751 - Afiliado al Débito")

            cuotas = 12 if safe_int(ctx.credito.numero_de_cuotas) > 12 else safe_int(ctx.credito.numero_de_cuotas)
            page.locator("#numero-de-cuotas_input").click()
            logging.info("🖱️ Clic en 'número de cuotas'")
            time.sleep(0.3)
            page.locator("li").filter(has_text=re.compile(rf"^\s*{cuotas}\s*$")).first.click()
            logging.info(f"⌨️ Seleccionando '{cuotas}' cuotas")

        # 1. Clic en Cotizar y confirmar
        page.get_by_role("button", name="Cotizar").click()
        logging.info("🖱️ Clic en 'Cotizar'")
        page.locator("#confirmar-aceptar").get_by_text("Sí").click()
        logging.info("🖱️ Clic en 'Sí'")
        logging.info("⏳ Esperando que cargue la cotización...")

        # 2. Esperar vista de resultados
        page.wait_for_load_state("networkidle")
        logging.info("✅ Cotización cargada exitosamente")

        # 3. Localizar enlace PDF dinámico
        enlace_pdf = page.locator("a", has_text=re.compile(r"\.pdf", re.IGNORECASE)).first
        enlace_pdf.wait_for(state="visible", timeout=60000)

        # 4. Capturar descarga
        with page.expect_download() as download_info:
            enlace_pdf.click()
            logging.info("🖱️ Clic en enlace PDF")
        download = download_info.value

        ruta_final = os.path.join(ruta_carpeta, f"cot_pos_{ctx.id_cot}.pdf")
        download.save_as(ruta_final)

        # 5. Validar guardado
        if os.path.exists(ruta_final) and os.path.getsize(ruta_final) > 0:
            cotizacion = True
            logging.info(f"✅ Cotización descargada exitosamente")
        else:
            raise Exception("No se descargó la cotización de Positiva")

    except PlaywrightError as e:
        error = True
        logging.info("--------------------------------")
        logging.error(f"❌ Error técnico de Playwright")
        logging.exception(e)
        msj_error = "Problemas técnicos, comunícate con el área de sistemas"
    except Exception as e:
        error = True
        logging.info("--------------------------------")
        logging.warning(f"⚠️ Error funcional : {e}")
        msj_error = str(e)
    finally:
        if error:
            tomar_captura(page, ruta_carpeta, f"ErrorCotizando_{ctx.id_cot}")
            if entorno:
                mensaje = f"""Hubo problemas para realizar la cotización en Positiva:
📋 Registro: {ctx.id_cot}
⚠️ Motivo: {msj_error}"""
                enviar_x_wsp(ctx, "notificacion", mensaje, None)
            renombrar_carpeta(ruta_carpeta)

        if cotizacion:
            archivo = os.path.join(ruta_carpeta, f"cot_pos_{ctx.id_cot}.pdf")
            if entorno:
                enviar_documento(ctx.id_cot, archivo, "cotizacion")
                enviar_x_wsp(ctx, "documento", None, archivo)

# ------------------ BUCLE PRINCIPAL DEL WORKER PERSISTENTE ------------------

def handle_shutdown(signum, frame):
    logging.info(f"🛑 Señal recibida ({signum}). Solicitando detención elegante del Worker...")
    stop_requested.set()

def main():

    global jobs_processed_count

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    logging.info("==================================================")
    logging.info(f"🚀 Iniciando Worker Persistente: {WORKER_ID}")
    logging.info(f"📦 Cola Redis asignada: {QUEUE_NAME}")
    logging.info(f"🖥 noVNC Puerto Host: {PUERTO}")
    logging.info("==================================================")

    # 🔴 Conexión a Redis
    r_conn = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True,
        socket_timeout=None,
        socket_connect_timeout=5,
        health_check_interval=30
    )

    # Estado inicial
    set_worker_status(r_conn, "STARTING")

    # Iniciar hilo de Heartbeat
    hb_thread = threading.Thread(target=heartbeat_thread_func, args=(r_conn,), daemon=True)
    hb_thread.start()

    with sync_playwright() as playwright:
        context, page = iniciar_navegador(playwright)

        # Inicializar sesión
        sesion_ok = inicializar_sesion(page, r_conn)
        if not sesion_ok:
            logging.warning("⚠️ CAPTCHA no resuelto en el arranque inicial. Navegador en página de login listo para la primera solicitud.")

        # Estado READY
        set_worker_status(r_conn, "READY")

        logging.info(f"👂 Worker {WORKER_ID} escuchando cola '{QUEUE_NAME}'...")

        while not stop_requested.is_set():
            try:
                # Asegurar estado READY mientras espera
                if current_status != "READY":
                    set_worker_status(r_conn, "READY")

                # Esperar Job de forma bloqueante (timeout corto para atender stop_requested)
                resultado = r_conn.brpop(QUEUE_NAME, timeout=5)

                if not resultado:
                    continue

                _, job_json = resultado
                job = json.loads(job_json)

                job_id = job.get("job_id", "SIN_ID")
                payload = job.get("payload", {})

                logging.info("--------------------------------------------------")
                logging.info(f"📥 Worker tomó Job {job_id}")
                set_worker_status(r_conn, "BUSY", job_id)

                # Procesar Job
                procesar_job(page, payload, job_id, r_conn)

                jobs_processed_count += 1
                logging.info(f"✅ Job {job_id} terminado (Total completados: {jobs_processed_count})")

                # Restaurar estado inicial del navegador para el siguiente Job
                reset_session(page, r_conn)
                set_worker_status(r_conn, "READY")

            except Exception as e:
                logging.error(f"❌ Error en bucle principal del Worker: {e}", exc_info=True)
                set_worker_status(r_conn, "ERROR")
                time.sleep(2)
                try:
                    reset_session(page, r_conn)
                except Exception as re_err:
                    logging.error(f"❌ Error crítico restableciendo sesión: {re_err}")

        # Shutdown
        logging.info("🛑 Deteniendo Worker y cerrando recursos...")
        set_worker_status(r_conn, "STOPPING")
        try:
            context.close()
        except Exception:
            pass
        set_worker_status(r_conn, "STOPPED")
        logging.info("🏁 Worker detenido limpiamente.")

if __name__ == "__main__":
    main()
