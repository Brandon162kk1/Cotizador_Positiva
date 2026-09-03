import os
import sys
import time
import logging
import base64

from Tiempo.fechas_horas import get_timestamp
from io import StringIO

#------Carpetas de Descargas y Volumen del Docker----------
nombre_carpeta_descargas = "Downloads"
download_path = f"/app/{nombre_carpeta_descargas}"

def obtener_imagenes_error(ruta_carpeta):

    imagenes_payload = []

    try:
        for archivo in os.listdir(ruta_carpeta):

            if archivo.startswith("ErrorCotizando_") and archivo.lower().endswith(".png"):

                ruta_completa = os.path.join(ruta_carpeta, archivo)

                with open(ruta_completa, "rb") as f:
                    imagen_base64 = base64.b64encode(f.read()).decode("utf-8")

                imagenes_payload.append(imagen_base64)

    except Exception as e:
        logging.error(f"❌ Error leyendo imágenes de la carpeta: {e}")

    return imagenes_payload

def esperar_archivos_nuevos(directorio, archivos_antes, extension, cantidad, timeout=180):
    """
    Espera archivos nuevos con determinada extensión.
    extension: ".zip", ".pdf", ".xlsx", etc.
    """

    inicio = time.time()

    while time.time() - inicio < timeout:
        actuales = set(os.listdir(directorio))
        nuevos = actuales - archivos_antes

        # Filtrar por extensión (case insensitive)
        nuevos = {
            f for f in nuevos
            if f.lower().endswith(extension.lower())
        }

        if len(nuevos) >= cantidad:

            # Validar que no estén en descarga (.crdownload)
            archivos_validos = []
            for f in nuevos:
                ruta = os.path.join(directorio, f)
                if not os.path.exists(ruta + ".crdownload"):
                    archivos_validos.append(ruta)

            if len(archivos_validos) >= cantidad:
                return archivos_validos

        time.sleep(1)

    return None

def renombrar_carpeta(ruta_carpeta):
    # Cerrar handlers del logger
    for handler in logging.getLogger().handlers[:]:
        handler.close()
        logging.getLogger().removeHandler(handler)

    # Obtener carpeta padre
    carpeta_padre = os.path.dirname(ruta_carpeta)

    # Obtener nombre actual
    nombre_actual = os.path.basename(ruta_carpeta)

    # Nuevo nombre
    nuevo_nombre = f"Error_{nombre_actual}"

    # Nueva ruta completa
    nueva_ruta = os.path.join(carpeta_padre, nuevo_nombre)

    # Renombrar
    os.rename(ruta_carpeta, nueva_ruta)

    #Actualizarla
    ruta_carpeta = nueva_ruta

def resolver_empresa(ctx):
    dispatch = {
        'dongfeng': 'Dongfeng',
        'pangu': 'Pangu',
        'zual': 'Zual'
    }

    org = (ctx.organizacion.nombre or "").lower()

    return next((v for k, v in dispatch.items() if k in org), 'Otro')

def crear_carpeta_descargas(ctx,entorno):

    # --- 👇 CREAR UN BUFFER NUEVO POR CADA CORREO ---
    log_buffer = StringIO()
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(log_buffer)],
        force=True
    )

    prefijo = "PRUEBAS_" if not entorno else ""

    organizacion = resolver_empresa(ctx)

    # 📁 rutas
    carpeta_base = os.path.join(download_path, f"{prefijo}Jishu_Car")
    carpeta_empresa = os.path.join(carpeta_base, organizacion)
    carpeta_movimientod = os.path.join(carpeta_empresa,ctx.movimiento.capitalize())
    carpeta_unica = os.path.join(carpeta_movimientod, f"{ctx.id_cot}_pos_{get_timestamp()}")

    # 🏗️ crear estructura completa
    os.makedirs(carpeta_unica, exist_ok=True)

    # 📝 log dentro de la carpeta final
    ruta_log = os.path.join(carpeta_unica, f"log_{get_timestamp()}.txt")

    # Logger definitivo (archivo para este registro y consola uniforme)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    # Consola stdout
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    root_logger.addHandler(console_handler)

    # Archivo log
    file_handler = logging.FileHandler(ruta_log, mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(file_handler)

    # --- Pasar logs temporales al archivo ---
    log_buffer.seek(0)
    for line in log_buffer.readlines():
        logging.info(line.strip())

    return carpeta_unica