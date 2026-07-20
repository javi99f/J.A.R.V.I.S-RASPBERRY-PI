# Historial de versiones

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
