# Historial de versiones

## Cambios para la version 0.2.1

- Corregida la actualización desde Python 3.13 para que no intente descargar un `tflite-runtime` incompatible.
- Conservada la instalación existente de openWakeWord en Raspberry Pi sin modificar el motor de activación.

## Cambios para la version 0.2.0

- El botón `UPDATE` muestra siempre el resultado y se recupera automáticamente si GitHub no responde.
- El micrófono vuelve a estar activo en cada arranque, aunque se silenciara en la sesión anterior.
- Añadido el control de Spotify mediante órdenes naturales de voz o texto.
- Añadidas la búsqueda y reproducción de canciones, artistas, álbumes y listas.
- Añadidos los controles naturales de pausa, reanudación, pista siguiente y pista anterior.
- Añadido un reproductor táctil compacto debajo del círculo con título, artista y controles manuales.
- Enrutadas todas las órdenes de volumen al volumen general de la Raspberry Pi, también mientras suena Spotify.
- Conectado el botón `UPDATE` directamente al actualizador local para que no dependa de una respuesta de Gemini.
- Priorizada la propia Raspberry Pi frente a teléfonos u otros dispositivos activos de Spotify.
- Añadida la instalación guiada de Raspotify para convertir la Raspberry en un altavoz Spotify Connect.
- Añadida una configuración OAuth local que no expone secretos en el historial de la terminal.
- Añadidos mensajes de diagnóstico para autorización pendiente, Spotify Premium y dispositivos desconectados.

## Cambios para la version 0.1.1

- Añadido un registro separado `ACCIONES DEV` con cada análisis, intento, herramienta y cambio sensible.
- Encadenadas las entradas de auditoría mediante SHA-256 para detectar alteraciones posteriores.
- Cada cambio real indica su identificador de auditoría y los archivos o ajustes modificados.
- Los análisis son de solo lectura y ya no pueden presentarse como correcciones aplicadas.
- En modo normal, Jarvis se limita a disculparse ante un fallo y no promete aprender, guardarlo o corregirlo.
- La memoria rechaza notas que pretendan cambiar el comportamiento interno de Jarvis.
- La escucha contextual de cinco segundos tiene un plazo fijo que el ruido o conversaciones ajenas no pueden ampliar.
- Reforzada la detección de `Hey Jarvis` mediante una confirmación doble o una coincidencia especialmente fuerte.
- Jarvis permanece en silencio cuando la conversación está dirigida a Siri, Alexa u otro asistente.

## Cambios para la version 0.1.0

- Establecida esta versión como la primera base funcional de Jarvis para Raspberry Pi.
- Añadida la interfaz táctil vertical para la pantalla de 600 por 1024 píxeles.
- Añadidos el historial de conversaciones, los errores y la selección de entrada y salida de audio.
- Añadido el actualizador remoto seguro mediante GitHub Releases.
- Añadido el modo desarrollador protegido mediante contraseña escrita.
- Añadida la personalización de la forma de hablar, personalidad y voz.
- Añadida la escucha contextual durante cinco segundos después de que Jarvis termina de hablar.
