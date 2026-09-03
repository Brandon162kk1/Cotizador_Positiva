FROM playwright-base:1.0

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# 2. Configuración propia de supervisord para este proyecto
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

COPY . .

# 4. Puerto que expone este proyecto (ej: 8088)
#EXPOSE 8088

# 5. Comando para levantar este contenedor
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]