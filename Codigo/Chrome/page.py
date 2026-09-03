import logging
import os
from Tiempo.fechas_horas import get_timestamp

def tomar_captura(page, ruta, prefijo):
    try:
        nombre = f"{prefijo}_{get_timestamp()}.png"
        ruta_completa = os.path.join(ruta, nombre)
        page.screenshot(path=ruta_completa, full_page=True)
    except Exception as e:
        logging.error(f"⚠️ No se pudo tomar la captura de pantalla: {e}")