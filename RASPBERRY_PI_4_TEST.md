# Prueba de JARVIS en Raspberry Pi 4 Model B

Esta versión está preparada para Raspberry Pi OS de 64 bits con escritorio. Se
recomiendan 4 GB de RAM o más, fuente oficial y refrigeración.

## 1. Instalar

Abre un terminal en la carpeta del proyecto:

```bash
chmod +x install_pi4.sh launch_assistant.sh assistantctl
./install_pi4.sh
```

El instalador crea `.venv`, instala audio, interfaz y el detector local, y
descarga una sola vez el modelo `hey jarvis`.

## 2. Configurar

```bash
nano .env
```

El único dato imprescindible es:

```dotenv
GEMINI_API_KEY=tu-clave-real
```

Configuración inicial recomendada:

```dotenv
WAKE_MODE=wakeword
WAKE_THRESHOLD=0.55
CONVERSATION_TIMEOUT_SECONDS=12
VOICE_RMS_THRESHOLD=300
```

OpenRouter, Zernio, Home Assistant y Bluetooth son opcionales.

## 3. Comprobar el micrófono

```bash
./.venv/bin/python audio_check.py
```

La utilidad enumera dispositivos y muestra el nivel RMS. Si el micrófono que
quieres usar no es el predeterminado, repite la prueba:

```bash
./.venv/bin/python audio_check.py --device NUMERO
```

Después guarda el número en `.env`:

```dotenv
INPUT_DEVICE=NUMERO
```

Prueba desde la distancia real, primero hablando normalmente y después diciendo
“Hey Jarvis”. Un RMS máximo inferior a 300 suele indicar una señal demasiado
baja; un valor cercano a 32767 indica saturación.

## 4. Arrancar

```bash
./launch_assistant.sh
```

Estados de privacidad:

- `STANDBY / LOCAL WAKE WORD ONLY`: el audio se procesa localmente y no se manda a Gemini.
- `LISTENING`: la conversación está abierta y sí se envía audio.
- `SPEAKING`: JARVIS está contestando.
- `MUTED`: no se procesa una activación.

Di “Hey Jarvis”, espera a que la pantalla cambie a `LISTENING` y formula la
pregunta. Después de la respuesta hay 12 segundos para continuar sin repetir la
activación. El botón `ACTIVAR` abre la misma ventana manualmente.

## 5. Ajustar falsas activaciones

- Si no se activa: prueba `WAKE_THRESHOLD=0.45`.
- Si se activa con conversaciones o televisión: prueba `WAKE_THRESHOLD=0.65`.
- Ajusta en pasos de 0.05 y realiza al menos 20 intentos desde varias posiciones.
- Para diagnosticar sin detector local usa temporalmente `WAKE_MODE=continuous`.
  Este modo envía audio continuamente y no debe quedar como configuración normal.

## 6. Pruebas mínimas

1. Conversación normal sin decir “Hey Jarvis”: no debe responder.
2. Decir “Hey Jarvis, qué hora es”: debe abrirse y responder.
3. Preguntar “¿y mañana?” después de una consulta meteorológica: debe conservar contexto.
4. Esperar más de 12 segundos: debe regresar a `STANDBY`.
5. Pulsar `MIC`: debe mostrar `MUTED` y no activarse.
6. Pulsar `ACTIVAR`: debe abrir una conversación sin pronunciar la palabra.

## 7. Recoger información si falla

Ejecuta estos comandos y conserva la salida:

```bash
uname -a
python3 --version
arecord -l
aplay -l
pactl list short sources
pactl list short sinks
./.venv/bin/python -m unittest discover -s tests -v
```

No compartas el archivo `.env`, ya que contiene claves privadas.

