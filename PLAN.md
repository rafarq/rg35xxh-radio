# Plan de implementación — RG35XX H Radio-Stream App

Este documento es el plan técnico. No contiene código ni pasos de instalación en la máquina de
desarrollo; describe qué se construirá, en qué orden y cómo se validará en el dispositivo real.
Los puntos marcados **[POR VERIFICAR]** requieren confirmación en el RG35XX H antes de darlos por
cerrados.

## 0. Decisión clave: motor de reproducción bundleado

Dado que ningún reproductor del firmware es utilizable (ver REQUISITOS.md §3), el requisito
central del proyecto es empaquetar un motor de audio AArch64 aislado, con su propia pila TLS,
dentro de `/mnt/mmc/Roms/APPS/radio/engine/`.

Candidatos a evaluar, en orden de preferencia pragmático:

1. **Binario estático de `ffmpeg`/`ffprobe` para AArch64 (linux-arm64, static build)**, invocado
   como subproceso vía `ffmpeg -> pipe PCM -> ALSA` o directamente `ffmpeg` con salida `alsa`.
   Ventaja: build estático incluye su propio OpenSSL/GnuTLS y demuxers (mp3, aac, hls) sin
   depender de Pango (el fallo del sistema es de una dependencia de *filtros de subtítulos/UI*,
   irrelevante para audio puro sin overlay de texto). Es la opción más simple de aislar: un solo
   binario, sin libs de sistema.
2. **`libmpv` estático o `mpv` estático para AArch64**, controlado vía `python-mpv` (JSON IPC) en
   vez del `mpv` roto del firmware. Ventaja: mejor manejo nativo de HLS y reconexión. Requiere
   validar que el build estático no reintroduzca el mismo problema de símbolos OpenSSL 1.1 que
   el mpv del firmware (por eso se descarta el `mpv` del sistema, no el concepto de mpv).
3. Fallback: `mpg123`/`faad2`/parser HLS a mano — descartado salvo que 1 y 2 fallen, por el coste
   de reimplementar demuxing HLS.

**Elección para el plan de trabajo:** empezar por (1), ffmpeg estático AArch64 como subproceso,
por ser el camino de menor riesgo (un solo binario, formatos ya cubiertos, TLS propio). Se deja
(2) como plan B documentado si en pruebas reales el arranque/latencia de `ffmpeg` por subprocess
resulta pobre para cambios rápidos de emisora.

**[VERIFICADO]** en el dispositivo: un binario estático de ffmpeg 9.0.1 AArch64 se ejecuta
correctamente (el mismatch de ELF era propio del `ffplay` de vendor, no de la arquitectura/kernel
del dispositivo) y decodifica streams MP3, AAC y HLS reales del playlist con verificación TLS
activa, siempre que `SSL_CERT_FILE` apunte al `cacert.pem` bundleado (ver REQUISITOS.md §3).

**[VERIFICADO]** ese mismo binario fue compilado sin el muxer de salida `alsa`
("Requested output format alsa is not known."), así que no puede emitir audio por ALSA
directamente. La arquitectura de reproducción final usa **dos** subprocesos por emisora, ambos
lanzados como listas de argumentos (`shell=False`), nunca una pipeline de shell: el ffmpeg
bundleado decodifica la URL a PCM crudo (44.1kHz estéreo s16le) por `pipe:1`, y el `aplay` del
sistema recibe ese PCM por stdin — con flags de formato explícitos que coinciden exactamente con
la salida del decoder — y lo reproduce por ALSA. Los dos procesos se conectan mediante una pipe
de sistema operativo pasada directamente a `Popen` (`stdout` del decoder como `stdin` del
reproductor), y sus ciclos de vida se gestionan juntos (`terminate()` → timeout → `kill()` →
`wait()` de ambos) para no dejar zombies ni pipes/sockets abiertos al cambiar de emisora.

## 1. Estructura del paquete instalado

```
/mnt/mmc/Roms/APPS/Radio.sh              # launcher, invoca python3 sobre radio/main.py
/mnt/mmc/Roms/APPS/radio/
  main.py                                # entrypoint
  ui/                                    # capa SDL2 + Pillow
  playlist/                              # parser M3U, modelo de categorías
  playback/                              # wrapper del motor (subprocess ffmpeg), lifecycle
  input/                                 # lectura de /dev/input/event1, mapeo de botones
  data/
    playlist.m3u                         # las 1.041 entradas / 24 grupos
    favorites.json                       # persistente
    recent.json                          # persistente
    config.json                          # config de usuario (volumen, último grupo, etc.)
  engine/
    ffmpeg-aarch64                       # binario estático bundleado
  vendor/                                # dependencias Python vendorizadas si no están en el
                                          # sistema (evitar pip install en el dispositivo)
  assets/                                # iconos, fuentes para Pillow
```

Todo lo escribible (favorites.json, recent.json, config.json, logs) vive bajo `radio/data/`, en
la SD. No se toca nada fuera de este árbol.

## 2. Fases de trabajo

### Fase 1 — Fundamentos sin UI
- Parser M3U extendido: lee `playlist.m3u`, produce lista de entradas
  `(título, url, grupo, tipo_stream_inferido)`, tolera líneas malformadas (se saltan con log, no
  abortan). Test unitario contra el archivo real de 1.041 entradas / 24 grupos.
- Modelo de datos de categorías (24 grupos) y de favoritos/recientes (lectura/escritura JSON en
  `data/`).
- Wrapper de reproducción: lanza `ffmpeg-aarch64` (decodificador, sin muxer ALSA) y `aplay`
  (salida ALSA) como par de subprocesos conectados por pipe de SO, gestiona sus stdout/stderr,
  expone estados (conectando/reproduciendo/error/detenido), mata ambos procesos de forma
  limpia (`terminate` + timeout + `kill` si no responden) al cambiar de emisora o salir.
- Punto de decisión temprano: **[POR VERIFICAR]** confirmar en el dispositivo, con las tres
  fases de este punto ya construidas pero sin UI (script de línea de comandos mínimo), que se
  puede: (a) ejecutar el binario ffmpeg estático, (b) que emite audio por ALSA interno, (c) que
  reproduce correctamente al menos una URL MP3-HTTPS, una AAC-HTTPS y una HLS del playlist real.
  Este punto de control debe pasar antes de invertir en la capa de UI.

### Fase 2 — Input y ciclo de vida de proceso
- Lectura de `/dev/input/event1` (vía `python-evdev` vendorizado o parseo directo de la
  estructura `input_event` con `struct`, para minimizar dependencias externas).
- Mapeo de botones **[POR VERIFICAR]** contra hardware real antes de fijar constantes.
- Bucle principal no bloqueante: la lectura de input, el refresco de UI y el polling de estado
  del subproceso de audio deben convivir en el mismo loop (o en hilos separados con colas
  thread-safe) sin que la conexión/buffering de un stream congele el redibujado de pantalla ni
  corte el audio que ya estaba sonando.
- Pruebas de estrés: cambiar de emisora repetidamente (p. ej. 30 cambios seguidos) verificando
  que no quedan procesos `ffmpeg` huérfanos ni descriptores de socket abiertos de más
  (`/proc/<pid>/fd` del proceso principal antes/después).

### Fase 3 — UI (PySDL2 + Pillow, 640x480 lógicos)

640x480 es la resolución lógica objetivo heredada de la referencia del proyecto (ver
REQUISITOS.md §2), no necesariamente una medida tomada del panel físico del dispositivo; es el
tamaño sobre el que se construye y prueba `radio/ui/`.

- Pantalla de categorías (24 grupos).
- Pantalla de lista de emisoras dentro de un grupo, con scroll.
- Pantalla "reproduciendo": nombre de emisora, grupo, estado (conectando/en vivo/error),
  controles de volumen, favorito toggle.
- Pantalla de favoritos y de recientes.
- Búsqueda/filtro por nombre — **opcional**, se implementa después de que las pantallas
  anteriores estén validadas en dispositivo, usando entrada por teclado virtual con D-pad o,
  si el dispositivo lo permite, texto vía combinación de botones; si el coste de UX es alto para
  un mando sin teclado, se puede degradar a filtro por prefijo alfabético (saltar a la letra)
  como default más pragmático.
- Todo el renderizado se hace en Pillow y se sube a una textura SDL2; el hilo/loop de UI nunca
  espera de forma síncrona a que el stream conecte (esa espera vive en el wrapper de
  reproducción, consultado de forma no bloqueante).

### Fase 4 — Persistencia y UX de arranque
- Recordar última categoría/emisora vista (`config.json`).
- Favoritos y recientes ya cubiertos en Fase 1, aquí se conecta a la UI (toggle de favorito,
  vista de recientes con límite, p. ej., de 20 entradas, orden más-reciente-primero).
- Manejo de error de red por entrada individual: si una URL falla, mostrar mensaje claro en
  pantalla y permitir volver a la lista sin colgar la app; no debe detener la navegación global.

### Fase 5 — Empaquetado e instalación
- Generar `Radio.sh` (shell wrapper mínimo que invoca `python3 radio/main.py` con el `PATH`/
  `LD_LIBRARY_PATH` apuntando solo a lo bundleado si hiciera falta, para no interferir con el
  resto del firmware).
- Verificar permisos de ejecución del `.sh` y del binario `ffmpeg-aarch64` sobreviven una copia
  típica a SD vía USB/lector (FAT32 no siempre preserva bit ejecutable) — **[POR VERIFICAR]**;
  si no lo preserva, el propio `Radio.sh` debe hacer `chmod +x` sobre el binario del engine en su
  primer arranque, ya que eso sí es una escritura permitida dentro de `radio/`.
- Confirmar convención de icono/nombre que espera el launcher de menú del firmware
  **[POR VERIFICAR]**.

## 3. Defaults de UX pragmáticos

- Volumen inicial: nivel medio (p. ej. 60%), ajustable con D-pad izq/der en pantalla de
  reproducción; se persiste en `config.json`.
- Al reproducir, timeout de conexión razonable (p. ej. 8–10s) antes de mostrar error, en vez de
  esperar indefinidamente — cifra exacta a ajustar tras pruebas de latencia real de red del
  dispositivo **[POR VERIFICAR]**.
- Recientes limitado a las últimas ~20 emisoras reproducidas, sin duplicados (reproducir de
  nuevo una ya presente la mueve al tope).
- Búsqueda por texto es *nice-to-have*; el salto alfabético por letra (mantener pulsado un botón
  para ciclar A→Z) es el mecanismo por defecto de filtrado rápido, más adecuado a un mando sin
  teclado físico.
- Sin pantalla de carga bloqueante: al entrar en "reproduciendo" se muestra la UI de inmediato
  con estado "conectando…" y el audio arranca en cuanto el subproceso empieza a emitir PCM.

## 4. Plan de pruebas de aceptación (dispositivo real)

Todas se ejecutan en el RG35XX H físico, no en desarrollo de escritorio, por las diferencias de
ABI descritas en REQUISITOS.md §3.

1. Reproducción real de una emisora MP3-HTTPS del playlist con audio audible.
2. Reproducción real de una emisora AAC-HTTPS.
3. Reproducción real de un stream HLS (`.m3u8`), incluyendo al menos una transición de segmento
   sin corte audible perceptible.
4. Reproducción real de un stream Icecast genérico (sin extensión, cabeceras `icy-*`).
5. Parseo completo del `playlist.m3u`: conteo de 1.041 entradas y 24 grupos coincide con lo
   esperado.
6. Cambio de emisora 30 veces seguidas sin procesos `ffmpeg` huérfanos ni fugas de descriptores.
7. Ciclo favorito: marcar, salir de la app, reabrir, favorito persiste.
8. Ciclo recientes: reproducir 3 emisoras distintas, verificar orden y persistencia tras
   reinicio de la app.
9. UI responsive durante conexión: mientras una emisora está "conectando", la navegación
   (volver atrás, moverse en la lista) sigue respondiendo sin bloqueo perceptible.
10. Emisora caída/URL muerta: la app muestra error y permite volver a navegar sin colgarse.

## 5. Riesgos y puntos de verificación pendientes (resumen)

- Ejecutabilidad real de un binario AArch64 estático de ffmpeg en el userland del dispositivo
  (el vendor ffplay tuvo mismatch de ELF; hay que confirmar que un build genérico linux-arm64
  estático sí corre).
- Mapeo de códigos de botón de `/dev/input/event1`.
- Preservación de permisos de ejecución al copiar a la SD vía FAT32.
- Convención exacta de integración con el menú de launcher del firmware (icono, metadata).
- Límites reales de RAM/CPU disponibles para la app tras el resto del firmware.
- Tiempo de arranque y latencia de conexión aceptables en la red real donde se use el
  dispositivo.
