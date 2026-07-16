# Actualizaciones remotas de J.A.R.V.I.S RASPBERRY PI mediante GitHub

Jarvis 0.4.0 incorpora un actualizador para Raspberry Pi que consulta
**GitHub Releases**. No ejecuta `git pull`, no modifica `.env` y no necesita
guardar una contraseña de GitHub cuando el repositorio es público.

## Qué se publica

Cada Release contiene exactamente estos dos archivos:

- `jarvis-pi-arm64.zip`: código y recursos ejecutables de Jarvis.
- `jarvis-pi-manifest.json`: versión, plataforma, tamaño y SHA-256 del ZIP.

El manifiesto y el paquete se generan automáticamente. No deben prepararse a
mano ni editarse después de publicar la Release.

## Protección de los datos de la Raspberry

El instalador nunca reemplaza estas ubicaciones:

- `.env` y todas las claves API.
- `.venv` y el intérprete local.
- `config/`, incluida la configuración visual y de dispositivos.
- `memory/` y los recuerdos guardados.
- `runtime/`, registros y estado del micrófono.
- `.git` y `.updates`.

Antes de sustituir código crea una copia en `.updates/backups/`. Después
compila e importa la nueva versión. Si esa validación falla, restaura la copia
automáticamente.

## Preparar el repositorio por primera vez

1. Crea en GitHub un repositorio llamado `J.A.R.V.I.S-RASPBERRY-PI`. Para la primera etapa se
   recomienda que sea público: la Raspberry podrá consultar Releases sin
   almacenar un token con acceso al repositorio.
2. Copia a ese repositorio todos los archivos de esta carpeta respetando
   `.gitignore`.
3. No subas `.env`, `.venv`, `outputs/`, `dist-release/`, claves, registros ni
   capturas privadas.
4. Configura una vez la Raspberry añadiendo a `~/Jarvis/.env`:

   ```env
   UPDATE_REPOSITORY=javi99f/J.A.R.V.I.S-RASPBERRY-PI
   UPDATE_ALLOW_PRERELEASE=0
   ```

5. Instala manualmente la versión 0.4.0 en la Raspberry. Esta es la versión
   puente que incorpora el actualizador; las posteriores ya podrán instalarse
   de forma remota.

## Publicar una actualización

1. Modifica el código y añade las novedades a `CHANGELOG.md`.
2. Aumenta el valor de `VERSION`, por ejemplo de `0.4.0` a `0.4.1`.
3. Ejecuta las pruebas:

   ```bash
   python -m unittest discover -s tests -q
   ```

4. Confirma los cambios en GitHub Desktop.
5. Crea y sube una etiqueta que coincida exactamente con la versión:

   ```bash
   git tag v0.4.1
   git push origin main --tags
   ```

6. La automatización `.github/workflows/release-pi.yml` ejecutará las pruebas,
   construirá los dos archivos y creará la GitHub Release.

También puede ejecutarse manualmente desde la pestaña **Actions**. En ese caso
genera un artefacto de prueba, pero no publica una Release estable.

## Utilizarlo desde Jarvis

El usuario puede escribir o decir:

- `Busca actualizaciones de Jarvis`.
- `¿Hay una nueva versión?`.
- `Instala la actualización` después de confirmar.

También existe un botón `UPDATE` en la barra inferior de la interfaz de la
Raspberry.

Jarvis informa de la versión disponible y solicita confirmación. Tras descargar,
verificar e instalar, sale con el código interno 75. `start_jarvis_pi.sh` reconoce
ese código y vuelve a iniciar Jarvis dentro de la misma sesión gráfica.

## Comandos de mantenimiento

Desde `~/Jarvis`:

```bash
./.venv/bin/python -m omar_ai_core.updater check
./.venv/bin/python -m omar_ai_core.updater status
./.venv/bin/python -m omar_ai_core.updater install --yes
./.venv/bin/python -m omar_ai_core.updater rollback --yes
```

La restauración también conserva `.env`, memoria y configuración local.

## Seguridad

- El repositorio está fijado en `.env`; la IA no puede proporcionar una URL
  arbitraria para ejecutar código.
- Solo se aceptan descargas HTTPS desde dominios de GitHub.
- El ZIP debe coincidir en tamaño y SHA-256 con el manifiesto.
- Se bloquean rutas absolutas, `..`, unidades de Windows y enlaces simbólicos.
- Una instalación requiere confirmación explícita del usuario.
- El actualizador no utiliza `sudo` ni guarda la contraseña del sistema.

El SHA-256 protege frente a descargas dañadas y cambios accidentales. Para un
despliegue público de alto riesgo se puede añadir posteriormente una firma
Ed25519 independiente del manifiesto.
