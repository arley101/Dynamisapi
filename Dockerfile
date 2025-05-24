# Dockerfile

# 1. Usar una imagen base oficial de Python.
# Python 3.11-slim es una buena opción para un tamaño de imagen razonable.
FROM python:3.11-slim

# 2. Establecer el directorio de trabajo dentro del contenedor.
WORKDIR /code

# 3. Establecer variables de entorno para Python (opcional pero recomendado).
#    - PYTHONDONTWRITEBYTECODE: Evita que Python escriba archivos .pyc (útil en contenedores).
#    - PYTHONUNBUFFERED: Asegura que la salida de print() y logging se muestre inmediatamente.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 4. Copiar el archivo de requisitos primero para aprovechar el caché de Docker.
#    Si requirements.txt no cambia, esta capa no se reconstruirá.
COPY requirements.txt .

# 5. Instalar las dependencias.
#    Usamos --no-cache-dir para reducir el tamaño de la imagen.
#    Considera añadir --upgrade pip aquí si es necesario.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 6. Copiar todo el código de la aplicación al directorio de trabajo /code.
#    Esto debe hacerse DESPUÉS de instalar las dependencias para un mejor uso del caché.
COPY ./app /code/app

# 7. Exponer el puerto en el que Uvicorn se ejecutará dentro del contenedor.
#    FastAPI/Uvicorn por defecto corre en el puerto 8000.
EXPOSE 8000

# 8. Comando para ejecutar la aplicación cuando se inicie el contenedor.
#    Usamos Gunicorn como un servidor de producción ASGI robusto, que a su vez usa Uvicorn workers.
#    -w 4: Número de workers (ajusta según los núcleos de tu CPU en producción, 2-4 por núcleo es una buena regla general).
#    -k uvicorn.workers.UvicornWorker: Especifica el tipo de worker.
#    -b 0.0.0.0:8000: Enlaza Gunicorn a todas las interfaces de red en el puerto 8000 dentro del contenedor.
#    app.main:app: Le dice a Gunicorn dónde encontrar tu instancia de la aplicación FastAPI
#                  (el objeto 'app' en el archivo 'main.py' dentro del directorio 'app').
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "app.main:app"]