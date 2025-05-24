# app/auth/__init__.py
# Inicializa el paquete 'auth'.
# Este paquete está destinado a contener toda la lógica relacionada con
# la autenticación y autorización para los endpoints de la API FastAPI,
# como la validación de tokens (ej. JWT), gestión de scopes/permisos,
# y dependencias de seguridad para las rutas.
# Actualmente, la autenticación principal para llamadas salientes a Azure se maneja
# con DefaultAzureCredential en http_client.py.