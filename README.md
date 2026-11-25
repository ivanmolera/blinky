# Blinky

Aplicación nativa de macOS para consultar tus cámaras Blink desde el Mac. La interfaz está hecha con SwiftUI y se conecta a Blink mediante un pequeño proceso Python con [blinkpy](https://github.com/fronzbot/blinkpy). Es un proyecto independiente, sin afiliación con Blink o Amazon.

## Requisitos

- macOS 14 o posterior.
- Xcode 15.3 o posterior, con Swift 5.10 o posterior.
- Python 3.10 o posterior para conectar una cuenta Blink. El modo de demostración no necesita Python.

## Probar la demostración

Desde la carpeta del proyecto:

```sh
./scripts/run.sh --demo
```

Blinky se abre con datos de demostración para explorar la interfaz sin conectar cámaras. Sin `--demo` muestra el inicio de sesión o restaura tu sesión guardada. También puedes abrir `Package.swift` con Xcode y ejecutar el esquema Blinky.

## Conectar tus cámaras

Prepara primero el entorno Python local:

```sh
./scripts/bootstrap.sh
./scripts/run.sh
```

El primer comando crea `.venv` e instala la versión de blinkpy indicada en `Sources/Blinky/Bridge/requirements.txt`. Prefiere Python 3.13/3.12/3.11/3.10 si están disponibles y utiliza `python3` como alternativa. Puedes elegirlo con `BLINKY_BOOTSTRAP_PYTHON=/ruta/a/python3 ./scripts/bootstrap.sh`. No instala herramientas del sistema ni necesita `sudo`.

En la aplicación, conecta tu cuenta Blink y completa la verificación que te solicite Blink. La conexión con una cuenta real requiere acceso a Internet. No hace falta mantener la aplicación abierta para seguir utilizando la aplicación oficial de Blink.

## Crear una aplicación para Finder

```sh
./scripts/build-app.sh
open dist/Blinky.app
```

El script compila en modo release y crea `dist/Blinky.app`, con una firma ad hoc local. No instala la aplicación en `/Applications` ni emplea certificados de desarrollador.

Si has ejecutado `bootstrap.sh`, la aplicación generada guarda en `Info.plist` la ruta al intérprete de `.venv`. Debes conservar la carpeta del proyecto en esa ubicación. Para cambiar de ubicación, vuelve a preparar el entorno y a generar la aplicación. Este paquete es para uso local: todavía no incluye un intérprete Python distribuible ni está notarizado.

Para usar otro intérprete al ejecutar desde Terminal:

```sh
BLINKY_PYTHON=/ruta/a/python3 ./scripts/run.sh
```

El intérprete debe tener instaladas las dependencias del puente. `BLINKY_BRIDGE_PATH` permite indicar otra ruta a `bridge.py` durante el desarrollo.

## Alcance actual

Incluye inicio de sesión y verificación en dos pasos, restauración de sesión con el llavero, lista de sistemas y cámaras, petición de capturas y grabaciones, controles del sistema y de detección, y exportación manual de clips MP4. El puente real está implementado; todavía requiere validación con una cuenta y cámaras reales.

Los clips de esta versión proceden de la nube: hasta 50 clips de las últimas 48 horas, con un máximo de 20 MB por descarga. El acceso depende de las grabaciones disponibles para tu cuenta. Los clips guardados únicamente en USB todavía no están integrados. Consulta [el protocolo y sus límites](docs/bridge.md).

La vista de vídeo en directo todavía no está implementada. Blink utiliza servicios en la nube: esta aplicación no convierte las cámaras en cámaras locales ni proporciona grabación continua. La integración usa una biblioteca comunitaria y los cambios de autenticación o de protocolo de Blink pueden requerir una actualización. Actualizar imágenes o consultar cámaras con frecuencia puede aumentar el consumo de batería.

## Desarrollo y comprobaciones

```sh
swift test
swift build -c release
python3 -m unittest discover -s Tests/Python -p 'test_*.py'
```

Las pruebas del puente se ejecutan sin una cuenta Blink. La conexión real, el contenido de las cámaras y la autenticación de tu cuenta necesitan una comprobación manual.

No subas contraseñas, códigos de verificación, tokens, clips ni registros con datos privados al repositorio. `.venv`, compilaciones y archivos `.env` quedan excluidos mediante `.gitignore`.
