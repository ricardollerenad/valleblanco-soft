FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencias primero: se reutiliza la capa mientras requirements no cambie.
ARG REQUIREMENTS_FILE=requirements/dev.txt
COPY requirements/ requirements/
RUN pip install -r ${REQUIREMENTS_FILE}

COPY . .

# Sin privilegios de administrador dentro del contenedor.
RUN useradd --create-home app && chown -R app:app /app
USER app

EXPOSE 8000

# Por defecto: producción (2 workers por los 2 vCPU del VPS). En desarrollo
# docker-compose.yml lo sustituye por runserver.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
