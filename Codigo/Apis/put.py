import requests
import logging
import os

# --- Variables de Entorno ---
API_KEY_JC = os.getenv("API_KEY_JC")
API_BASE_URL_JC = os.getenv("API_BASE_URL_JC") 
PUERTO_HOST_JC = os.getenv("PUERTO_HOST_JC",API_BASE_URL_JC)

#-- Header Global para autenticación (si es necesario) --
headers = {
    "X-Api-Key": API_KEY_JC
}

def enviar_documento(id_movimiento, ruta_pdf,documento):

    logging.info("-----------------------------")
    logging.info(f"⌛ Enviando Cotización de Positiva a JishuCar al movimiento → {id_movimiento}")

    url = f"{API_BASE_URL_JC}/api/CotizacionApi/{id_movimiento}/{documento}/positiva"

    try:
        with open(ruta_pdf, "rb") as f:

            files = {
                'archivo': (
                    os.path.basename(ruta_pdf),
                    f,
                    "application/pdf"
                )
            }

            response = requests.put(
                url,
                headers=headers,
                files=files,
                timeout=30,
                verify=False 
            )

        if response.status_code in (200, 201, 204):
            logging.info(f"✅ {documento.capitalize()} enviada correctamente | Movimiento {id_movimiento}")
        else:
            logging.error(f"⚠️ Problemas enviando {documento.capitalize()} | Movimiento {id_movimiento} | Status {response.status_code} | Resp {response.text}")

    except Exception as e:
        logging.error(f"❌ Error enviando {documento.capitalize()} | Movimiento {id_movimiento} | {e}")
