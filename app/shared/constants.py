# app/shared/constants.py - Constantes de la aplicación
# app/shared/constants.py
"""
Este módulo está destinado a albergar constantes compartidas que son verdaderamente
fijas y no dependen del entorno de despliegue (es decir, no se cargan desde
variables de entorno o archivos .env).

Actualmente, la mayoría de las configuraciones y "constantes" que pueden variar
o que son sensibles (como endpoints de API, timeouts por defecto, nombres de
recursos configurables, credenciales, etc.) se gestionan a través de la instancia
`settings` importada desde `app.core.config`. Dicha instancia carga sus valores
desde variables de entorno y el archivo .env, lo que permite una configuración
flexible por entorno.

Si en el futuro se identifican constantes que son intrínsecas a la lógica de la
aplicación, no cambian entre entornos y son utilizadas por múltiples módulos,
este sería un lugar apropiado para definirlas.

Ejemplo de una constante que podría ir aquí si fuera necesaria:
# SUPPORTED_IMAGE_FORMATS = frozenset(["png", "jpeg", "jpg"])
# DEFAULT_THUMBNAIL_SIZE = (128, 128)

Por el momento, este archivo sirve como placeholder para tales constantes futuras
y para clarificar que las configuraciones dinámicas se manejan en `app.core.config`.
"""

# No se definen constantes aquí por ahora, ya que las configuraciones relevantes
# se manejan a través de `settings` para permitir la personalización por entorno.