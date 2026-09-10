import requests
import logging
import os
import base64

# --- Variables de Entorno ---
url_n8n_base = os.getenv("url_n8n_base")
puerto_n8n = os.getenv("puerto_n8n")

if puerto_n8n:
   url_n8n_base = f"{url_n8n_base}:{puerto_n8n}"

webhook_wsp = os.getenv("webhook_wsp")

url_n8n_wsp = f"{url_n8n_base}{webhook_wsp}"

def enviar_x_wsp(ctx=None, tipo="notificacion", mensaje=None, archivo=None, telefono=None):

    logging.info("-----------------------------")
    logging.info("📲 Enviando Notificación por WhatsApp")

    # Intentar obtener teléfono de ctx si no se pasó directamente
    if not telefono and ctx:
        try:
            celular = getattr(ctx.ejecutivo, "celular", None) if hasattr(ctx, "ejecutivo") else None
            if not celular and hasattr(ctx, "cliente"):
                celular = getattr(ctx.cliente, "celular", None)
            telefono = celular
            logging.info(f"Celular obtenido del contexto: {telefono}")
        except Exception:
            telefono = None
    if not telefono:
        telefono = os.getenv("celular_emergencia")
        logging.warning(f"⚠️ No se encontró número de contacto, utilizando Telefono de emergencia: {telefono}")

    if not telefono or str(telefono).strip().lower() in ("none", ""):
        logging.info("⚠️ No se pudo enviar WhatsApp: Teléfono no definido")
        return

    telefono = str(telefono).strip()

    if not telefono.startswith("51"):
        telefono = "51" + telefono

    payload = {
        "instancia": f"{os.getenv('instancia')}",
        "telefono": telefono,
    }

    if tipo == "notificacion":
        payload["tipo"] = "sendText"
        payload["mensaje"] = mensaje
    elif tipo == "documento":

        if not archivo:
            logging.error("⚠️ No se recibió el archivo de cotización")
            return

        if not os.path.exists(archivo):
            logging.error(f"❌ No existe el archivo: {archivo}")
            return

        try:
            with open(archivo, "rb") as f:
                archivo_base64 = base64.b64encode(f.read()).decode("utf-8")

            payload["archivo"] = archivo_base64
            payload["nombreArchivo"] = os.path.basename(archivo)
            payload["mimetype"] = "application/pdf"
            payload["tipo"] = "sendMedia"

            id_cot = getattr(ctx, "id_cot", "") if ctx else ""
            payload["mensaje"] = f"📋 Adjunto cotización de Positiva del registro {id_cot}."

        except Exception as e:
            logging.error(f"❌ Error convirtiendo PDF a Base64: {e}")
            return
    else:
        logging.info(f"❌ Tipo de mensaje no soportado: {tipo}")
        return

    try:
        response = requests.post(url_n8n_wsp, json=payload, timeout=30)

        if response.status_code in (200, 201, 204):
            logging.info("✅ Notificación enviada por Evolution API")
        else:
            logging.info(f"⚠️ Problemas en el envio de notificación a Evolution API - {response.status_code} - {response.text}")

    except Exception as e:
        logging.info(f"❌ Error enviando la notificación por el webhook, Motivo : {e}")
