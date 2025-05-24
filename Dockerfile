# Dockerfile

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PIP_NO_CACHE_DIR off
ENV PIP_DISABLE_PIP_VERSION_CHECK on

WORKDIR /code

COPY requirements.txt .

# Instalar dependencias, asegurando que pip esté actualizado
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

COPY ./app /code/app
# Si tienes otros directorios en la raíz que tu app necesita (ej. 'static'), cópialos aquí:
# COPY ./static /code/static

EXPOSE 8000

# Para depurar si gunicorn o uvicorn no se encuentran:
# Descomenta la siguiente línea y comenta el CMD original para probar.
# Esta línea intentará mostrar las versiones, si falla aquí, el problema es la instalación.
# CMD ["sh", "-c", "echo 'Checking gunicorn...' && gunicorn --version && echo 'Checking uvicorn...' && uvicorn --version && echo 'Checks done. Will not start app.' && sleep infinity"]

# Comando de Producción
# Usar un entrypoint para gunicorn y pasar los argumentos de la app como CMD puede ser más flexible
# Pero para este caso, un CMD directo es suficiente y más común con App Service.
# Asegurarse que gunicorn y uvicorn estén en el PATH del contenedor
# El comando se ejecutará como: gunicorn -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 app.main:app --log-level debug --access-logfile - --error-logfile -
CMD ["gunicorn", \
     "-w", "2", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--log-level", "debug", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app.main:app"]