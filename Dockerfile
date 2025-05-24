# Dockerfile

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PIP_NO_CACHE_DIR off
ENV PIP_DISABLE_PIP_VERSION_CHECK on

WORKDIR /code

COPY requirements.txt .

# Instalar dependencias y verificar gunicorn/uvicorn
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    echo "--- Verificando gunicorn (debería estar en /usr/local/bin/gunicorn) ---" && \
    ls -l /usr/local/bin/gunicorn && \
    echo "--- Ejecutando gunicorn --version ---" && \
    /usr/local/bin/gunicorn --version && \
    echo "--- Verificando uvicorn (debería estar en /usr/local/bin/uvicorn) ---" && \
    ls -l /usr/local/bin/uvicorn && \
    echo "--- Ejecutando uvicorn --version ---" && \
    /usr/local/bin/uvicorn --version && \
    echo "--- Listando paquetes instalados (pip list) ---" && \
    pip list && \
    echo "--- Contenido de /usr/local/bin/ ---" && \
    ls -l /usr/local/bin

COPY ./app /code/app

EXPOSE 8000

# Comando de inicio usando la ruta absoluta a gunicorn
CMD ["/usr/local/bin/gunicorn", \
     "-w", "2", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--log-level", "debug", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app.main:app"]