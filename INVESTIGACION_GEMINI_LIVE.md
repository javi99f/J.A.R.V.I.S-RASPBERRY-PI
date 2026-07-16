# Investigacion del error de Gemini Live en Raspberry Pi

Fecha: 16 de julio de 2026

## Conclusion

El error actual no lo provoca el microfono, ALSA, la voz, el modelo, las
herramientas ni la configuracion cognitiva de Jarvis. El registro termina en:

```text
TimeoutError: timed out during opening handshake
```

Ese punto corresponde al HTTP Upgrade que abre el WebSocket. El SDK solo envia
el modelo y la configuracion de Jarvis despues de que ese paso termina. Por
tanto, el fallo actual esta en el transporte WebSocket o en algo que actua
durante su apertura.

## Hechos comprobados

1. La nueva clave estandar empieza por `AIza`, tiene 39 caracteres y aparece
   una sola vez en `.env`.
2. Una llamada HTTPS directa a `GET /v1beta/models` devolvio HTTP 200. La clave,
   el proyecto, DNS, TLS y el acceso REST funcionan.
3. La clave anterior `AQ...` produjo un cierre WebSocket 1008 con un mensaje de
   credenciales no admitidas. Aunque esa clave no servia, este resultado prueba
   que la misma Raspberry, el mismo SDK y la misma red llegaron a abrir antes
   un WebSocket contra Gemini.
4. La fotografia del timeout muestra `runtime.py`, linea 682. Esa linea coincide
   exactamente con `Jarvis-Pi-600x1024-fix-v2.zip`, anterior al primer parche de
   transporte. El registro fotografiado no demuestra que el parche posterior
   de IPv4 llegara a ejecutarse.
5. `google-genai 1.75.0` y `websockets 16.1` son compatibles segun las
   dependencias declaradas. La version actual de `google-genai` es 2.11.0, pero
   su implementacion Live sigue delegando la apertura al mismo
   `websockets.connect` sin exponer ajustes de ruta o proxy. Actualizar el SDK a
   ciegas no garantiza corregir este timeout.
6. La aplicacion reintentaba cualquier error cada tres segundos para siempre.
   Con la primera clave invalida pudo generar muchos intentos consecutivos. Es
   un defecto real, aunque por si solo no demuestra que Google aplicara una
   limitacion silenciosa.

## Causas que siguen siendo posibles

Ordenadas por utilidad para el siguiente diagnostico:

1. Un proxy detectado automaticamente por `websockets 16.1`. Desde la version
   15, la libreria usa proxies del sistema y del entorno salvo que se indique
   `proxy=None`.
2. Una ruta IPv6 anunciada pero no funcional, o una seleccion de direccion que
   se queda bloqueada antes de probar IPv4. Hay un caso reproducido en Linux con
   el mismo traceback.
3. Diferencia entre la autenticacion por cabecera `x-goog-api-key` que usa el
   SDK y la autenticacion `?key=` que muestra la documentacion oficial para el
   WebSocket directo.
4. Limitacion o problema temporal en el borde de Gemini Live despues de muchos
   reintentos. La API normalmente responderia 429, por lo que no puede afirmarse
   sin medirlo.
5. Un intermediario de red que permite HTTPS normal pero atasca el Upgrade
   WebSocket. Es menos probable porque esa Raspberry ya consiguio abrirlo con
   la clave anterior, pero no queda descartado si la ruta cambio.

## Causas descartadas para este traceback concreto

- Clave ausente o mal copiada: REST dio 200.
- Facturacion obligatoria: Gemini 3.1 Flash Live Preview dispone de nivel
  gratuito.
- Modelo retirado: `gemini-3.1-flash-live-preview` esta vigente y declara
  soporte para Live API.
- Configuracion de Jarvis, tools o thinking: aun no se habian enviado.
- Microfono y altavoz: sus tareas se crean despues de conectar.
- Certificados o reloj gravemente incorrectos: HTTPS autenticado funciona.

## Instrumentacion preparada

`diagnose_pi_live.sh` ejecuta una sola bateria y guarda
`jarvis-live-diagnostic.txt` sin mostrar ni almacenar la clave. Comprueba por
separado:

- DNS, reloj y TLS;
- IPv4 e IPv6;
- proxies activos;
- autenticacion REST y visibilidad del modelo;
- WebSocket oficial con clave en URL;
- WebSocket con clave en cabecera;
- SDK con resolucion automatica e IPv4 directo;
- configuracion minima y configuracion completa de Jarvis.

Con esa salida se podra elegir el arreglo definitivo sin volver a cambiar
claves ni probar comandos al azar.

## Correcciones incorporadas en el codigo local

- Proxy desactivado por defecto en Raspberry Pi.
- IPv4 primero en Raspberry Pi, conservando la ruta automatica como alternativa.
- Compatibilidad con la configuracion anterior `LIVE_FORCE_IPV4`.
- Reintentos progresivos de 3, 6, 12, 24 y 30 segundos.
- Estado ERROR estable, sin parpadeo entre ERROR y THINKING.
- Registro persistente del traceback.
- 26 pruebas automatizadas superadas y `compileall` correcto.

## Fuentes

- WebSocket oficial de Gemini Live:
  https://ai.google.dev/gemini-api/docs/live-api/get-started-websocket
- Referencia de Live API:
  https://ai.google.dev/api/live
- Modelo Gemini 3.1 Flash Live Preview:
  https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-live-preview
- Precios y nivel gratuito:
  https://ai.google.dev/gemini-api/docs/pricing
- Claves de Gemini y migracion de claves estandar:
  https://ai.google.dev/gemini-api/docs/api-key
- Proxies automaticos de websockets 16.1:
  https://websockets.readthedocs.io/en/latest/topics/proxies.html
- Caso de timeout por IPv6 en Linux:
  https://github.com/google/adk-python/issues/299
- Estrategia oficial de reintentos:
  https://ai.google.dev/gemini-api/docs/troubleshooting
