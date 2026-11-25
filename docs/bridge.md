# Conector local de Blink

Blinky utiliza `blinkpy==0.25.9`, una biblioteca comunitaria para la nube de Blink. La app nativa ejecuta `bridge.py` como proceso hijo, sin abrir un puerto HTTP. Se requiere Python 3.10 o posterior; el entorno se prepara con `scripts/bootstrap.sh`.

## Protocolo

Cada petición y respuesta ocupa una línea JSON. Petición: `{"id":"identificador","method":"health","params":{}}`. Respuesta: `{"id":"identificador","result":{...}}` o `{"id":"identificador","error":{"code":"codigo","message":"mensaje seguro"}}`.

El canal contiene datos confidenciales y no debe registrarse. El puente descarta stdout/stderr de bibliotecas externas y silencia sus logs, que pueden contener direcciones autenticadas o identificadores. No acepta rutas de descarga ni URLs proporcionadas por el cliente: `download_clip` solo acepta el ID de un clip previamente descubierto en la cuenta.

| Método | Parámetros | Resultado |
| --- | --- | --- |
| `health` | Ninguno | Versiones de puente/Python/blinkpy y disponibilidad del conector; funciona sin blinkpy instalado. |
| `login` | `username`, `password` | `status`, `message` y `session`; puede requerir `requires_2fa`. |
| `verify` | `code` | Completa la verificación iniciada por `login`. |
| `restore` | `session` | Restaura una sesión mediante su refresh token, sin contraseña. |
| `session` | Ninguno | Última sesión, incluidos los tokens renovados durante una operación. |
| `logout` | Ninguno | Descarta la sesión del proceso y cierra las conexiones locales. |
| `snapshot` | Ninguno | `systems`, `cameras`, `clips`, `refreshedAt`. |
| `set_system_armed` | `system_id`, `armed` (booleano) | Solicita activar/desactivar el sistema. |
| `set_motion_enabled` | `camera_id`, `enabled` (booleano) | Solicita activar/desactivar la detección de movimiento. |
| `refresh_thumbnail` | `camera_id` | Solicita una nueva imagen a la cámara. |
| `record` | `camera_id` | Solicita un clip; no inicia grabación continua. |
| `download_clip` | `clip_id` | `dataBase64`, `mimeType`, `filename` para exportación por la app. |

`session` es una cadena que contiene un objeto JSON, preparada para el llavero de macOS. El puente no crea archivos de credenciales, sesiones ni medios. La contraseña y el PIN se eliminan al finalizar la autenticación y se excluyen de la sesión serializada. El cliente debe guardar la sesión de `login`/`verify`/`restore` y volver a consultar `session` después de operaciones para persistir la rotación de tokens. Un refresh token revocado exige volver a iniciar sesión; no hay reintento automático con contraseña.

Los IDs de cámaras son opacos: `<network_id>:<camera_type>:<camera_id>`. Los estados `armed` y `motionEnabled` pueden ser `null` cuando Blink no los informa; no deben interpretarse como desactivados. `updatedAt` solo se proporciona si puede extraerse la fecha de la imagen de Blink; `refreshedAt` indica la actualización del estado, no la fecha de captura de todas las imágenes.

## Límites de la primera versión

- Estado y lista de clips: caché de al menos 60 segundos. Un cambio explícito permite una actualización adicional para comprobar el resultado. Las peticiones de nueva imagen o clip tienen un intervalo mínimo de 60 segundos por cámara. La detección no es una notificación en tiempo real.
- Clips: se consultan como máximo las dos primeras páginas de la nube (hasta 50 elementos) de las últimas 48 horas. El acceso depende de las grabaciones disponibles para la cuenta. Los clips solo almacenados en USB no se consultan en esta versión.
- Cada descarga se limita a 20 MB y la lectura HTTP se realiza por bloques. No se proporciona vídeo en directo, grabación continua, audio bidireccional ni descarga automática.
- `logout` es una desconexión local; no garantiza revocar tokens en los servidores de Blink. La app debe eliminar también la sesión del llavero.
- La biblioteca usa interfaces privadas de Blink, que pueden cambiar. Las pruebas automatizadas sustituyen la cuenta y las cámaras; la compatibilidad real requiere probar una cuenta del usuario.

## Fuentes verificadas

- [Release blinkpy v0.25.9](https://github.com/fronzbot/blinkpy/releases/tag/v0.25.9).
- [Autenticación y atributos de sesión de esa release](https://github.com/fronzbot/blinkpy/blob/v0.25.9/blinkpy/auth.py).
- [Inicio, verificación y metadatos de clips](https://github.com/fronzbot/blinkpy/blob/v0.25.9/blinkpy/blinkpy.py).
- [Métodos de cámara](https://github.com/fronzbot/blinkpy/blob/v0.25.9/blinkpy/camera.py).
