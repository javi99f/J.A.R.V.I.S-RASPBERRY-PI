# Historial de versiones

## 0.6.0

- Modo desarrollador local activable diciendo `Hey Jarvis, modo desarrollador`.
- Contraseña escrita y oculta; nunca se envía a Gemini ni aparece en el historial.
- Sesión de desarrollador temporal de 30 minutos y bloqueo breve tras tres intentos fallidos.
- Diagnóstico autónomo de errores recientes con credenciales redactadas.
- Cambios sensibles limitados a opciones autorizadas, con confirmación explícita.
- Nueva pestaña `PERSONA` para cambiar forma de hablar, personalidad y voz.
- Selección entre las voces compatibles de Gemini Live; `Charon` sigue siendo la predeterminada.
- Escucha contextual exacta de 5 segundos después de que Jarvis termina de hablar.
- La personalidad del usuario se guarda aparte y no puede borrar las reglas esenciales de seguridad.

## 0.5.0

- Nueva pestaña táctil `AJUSTES` adaptada a la pantalla vertical 600x1024.
- Historial persistente de órdenes, respuestas y mensajes del sistema.
- Pestaña de diagnóstico con los errores recientes del runtime.
- Selección de entrada de audio para elegir por dónde recibe la voz.
- Selección de salida de audio para elegir por dónde suena Jarvis.
- Cambio de dispositivos de audio sin reiniciar la Raspberry Pi.
- Búsqueda de dispositivos conectados después de iniciar Jarvis.
- Compatibilidad automática con dispositivos USB de 44,1 y 48 kHz.
- La interfaz muestra claramente la wake word `Hey Jarvis`.

## 0.4.0

- Añadido actualizador remoto para Raspberry Pi mediante GitHub Releases.
- Verificación obligatoria de tamaño y SHA-256 antes de instalar.
- Extracción segura contra rutas maliciosas y enlaces simbólicos.
- Copia de seguridad automática de todos los archivos sustituidos.
- Restauración automática si la instalación o la validación fallan.
- Conservación de `.env`, memoria, estado, configuración visual y dispositivos.
- Orden natural `busca actualizaciones` y herramienta de instalación con confirmación.
- Botón `UPDATE` en la interfaz táctil de Raspberry Pi.
- Reinicio controlado de Jarvis sin cerrar la sesión gráfica.
