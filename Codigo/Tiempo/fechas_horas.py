import pytz

from datetime import datetime,timedelta

# Definir la zona horaria de Lima
tz_peru = pytz.timezone("America/Lima")

def get_fecha_hoy():
    return datetime.now(tz_peru)

def get_timestamp():
    return datetime.now(tz_peru).strftime('%Y%m%d_%H%M%S')

def get_hora_minuto_segundo():
    return datetime.now(tz_peru).strftime("%H-%M-%S")

def get_fecha_actual():
    return datetime.now(tz_peru).strftime("%Y-%m-%d")

def get_fecha_dmy():
    return datetime.now(tz_peru).strftime("%d-%m-%Y")

def get_anio():
    return datetime.now(tz_peru).strftime("%Y")

def get_dia():
    return datetime.now(tz_peru).strftime("%d")

def get_mes():
    return datetime.now(tz_peru).strftime("%m")

def get_hora():
    return datetime.now(tz_peru).strftime("%H")

def get_minuto():
    return datetime.now(tz_peru).strftime("%M")

def get_pos_fecha_dmy():
    return datetime.now(tz_peru).strftime("%d/%m/%Y")

def get_fecha_menos_x_dias(dia):
    fecha = datetime.now(tz_peru) - timedelta(days=dia)
    return fecha.strftime("%d/%m/%Y")
