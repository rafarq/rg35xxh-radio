# Requisitos — RG35XX H Radio-Stream App

Estado de cada afirmación marcado como **[VERIFICADO]** (confirmado en el dispositivo/entorno real)
o **[POR VERIFICAR]** (asumido/pendiente de confirmar en el dispositivo).

## 1. Instalación y ubicación

- **[VERIFICADO]** La app se instala como launcher `/mnt/mmc/Roms/APPS/Radio.sh` más un
  directorio de soporte `/mnt/mmc/Roms/APPS/radio/` (código, assets, motor de reproducción
  empaquetado, playlist, datos persistentes).
- **[VERIFICADO]** Convención de icono del menú de firmware: un PNG de 240x180 en
  `/mnt/mmc/Roms/APPS/Imgs/Radio.png` (nombre `Imgs/<LauncherName>.png`), documentado en
  `radio/README.md` §"Firmware menu integration".
- **[REQUISITO]** No debe escribirse fuera de `/mnt/mmc/Roms/APPS/radio/` (ni en `/`, ni en
  particiones de sistema). Toda la persistencia (favoritos, recientes, config, logs) vive en la
  SD, dentro del directorio de la app.

## 2. Plataforma objetivo

- **[VERIFICADO]** Hardware: RG35XX H, CPU ARM64 (AArch64).
- **[VERIFICADO]** SO base: Ubuntu 22.04 (imagen del firmware del dispositivo).
- **[VERIFICADO]** Python 3.10.12 disponible en el sistema.
- **[VERIFICADO]** PySDL2 0.9.17 disponible/objetivo.
- **[VERIFICADO]** Pillow 9.0.1 disponible/objetivo.
- **[VERIFICADO]** Salida de audio vía ALSA interno (altavoz/jack del dispositivo).
- **[VERIFICADO]** Pantalla 640x480: resolución lógica objetivo heredada de la referencia del
  proyecto, usada por la UI de esta app (`radio/ui/app.py`); no es necesariamente una medida
  tomada directamente del panel físico del dispositivo, pero es el target sobre el que se
  construye y prueba la interfaz.
- **[VERIFICADO]** `SDL_CreateWindow` con tamaño explícito 640x480 y flag
  `SDL_WINDOW_FULLSCREEN` crashea en el dispositivo con `RuntimeError: SDL_CreateWindow failed:
  b"Could not initialize EGL"`. Una app de referencia conocida y funcional en el mismo
  dispositivo crea su ventana con `width=0, height=0` y flags
  `SDL_WINDOW_FULLSCREEN_DESKTOP | SDL_WINDOW_SHOWN`, y su launcher exporta
  `PYSDL2_DLL_PATH=/usr/lib`; `radio/ui/app.py` y `Radio.sh` siguen ese mismo patrón
  (la superficie lógica de 640x480 renderizada con Pillow se escala al tamaño real de ventana
  vía `SDL_RenderSetLogicalSize`, no pidiendo una ventana de 640x480 directamente).
- **[VERIFICADO]** Controles físicos leídos desde `/dev/input/event1`.
- **[POR VERIFICAR]** Mapeo exacto de códigos de tecla (`EV_KEY` codes) de `/dev/input/event1`
  a botones físicos (A/B/X/Y, D-pad, L1/R1/L2/R2, Start/Select, Menu/Power). Debe confirmarse
  con `evtest` o equivalente en el propio dispositivo antes de fijar el mapeo definitivo.
- **[POR VERIFICAR]** Si existen otros `/dev/input/eventN` relevantes (p. ej. un segundo
  dispositivo para volumen físico) que deban leerse también.
- **[POR VERIFICAR]** Espacio libre real en la SD del usuario para alojar el motor de
  reproducción empaquetado (tamaño del binario/runtime AArch64 + dependencias).
- **[VERIFICADO]** El binario estático de ffmpeg 9.0.1 AArch64 (`radio/engine/ffmpeg-aarch64`)
  se ejecuta y decodifica MP3/AAC/HLS en el dispositivo sin depender de la libssl rota del
  sistema, siempre que `SSL_CERT_FILE` apunte al `cacert.pem` bundleado (ver §3 y
  `radio/engine/README.md`).
- **[VERIFICADO]** Ese mismo binario fue compilado **sin muxer de salida ALSA**
  ("Requested output format alsa is not known."), por lo que no puede escribir audio
  directamente en ALSA aunque decodifique correctamente. La reproducción real usa dos
  procesos: el ffmpeg bundleado decodifica a PCM crudo (44.1kHz estéreo s16le) por stdout,
  y el `/usr/bin/aplay` del sistema (que sí soporta salida ALSA) consume ese PCM por stdin
  con flags de formato explícitos — ver §3 y `radio/engine/README.md`.

## 3. Reproductores de firmware — por qué no sirven [VERIFICADO]

- `mpv` del firmware: falla al resolver símbolos de OpenSSL 1.1 (binario enlazado contra una
  ABI de OpenSSL no presente/incompatible en el sistema) → no reproduce nada por HTTPS.
- `ffmpeg`/`ffplay` del sistema: fallan por dependencia de Pango faltante o rota → no arrancan.
- `ffplay` de vendor (incluido por el fabricante): mismatch de ELF (arquitectura/formato de
  binario incompatible con el userland real del dispositivo) → no ejecuta.
- `mplayer` de vendor: ejecuta, pero falla al abrir streams HTTPS tal como se probó (sin TLS
  funcional) → no puede abrir la inmensa mayoría de streams del playlist (HTTPS/HLS).
- **Conclusión [REQUISITO]**: ninguno de los reproductores preinstalados es utilizable tal cual.
  La app debe traer su propio motor de reproducción, aislado del sistema, compilado/empaquetado
  para AArch64, con una pila TLS propia y funcional.
- **[VERIFICADO]** Un binario estático de `ffmpeg` 9.0.1 para AArch64 (linux-arm64) sí se
  ejecuta en el userland del dispositivo (el mismatch de ELF era propio del `ffplay` de vendor,
  no de la arquitectura/kernel en general) y decodifica correctamente streams MP3, AAC y HLS,
  validando certificados TLS, siempre que se apunte `SSL_CERT_FILE` al `cacert.pem` bundleado en
  vez de depender de la pila OpenSSL rota del sistema.
- **[VERIFICADO]** Ese binario bundleado no incluye el muxer de salida `alsa` de ffmpeg, así
  que no puede emitir audio por ALSA directamente pese a decodificar bien. La arquitectura real
  de reproducción usa dos subprocesos encadenados por una pipe de sistema operativo (nunca una
  pipeline de shell): el ffmpeg bundleado decodifica a PCM crudo por `pipe:1`, y el `aplay`
  del sistema (que sí sabe hablar con ALSA y no necesita TLS propio) lo reproduce leyendo ese
  PCM por stdin con flags de formato explícitos que coinciden con la salida del decoder.

## 4. Playlist

- **[VERIFICADO]** Formato: Extended M3U (`#EXTM3U`, `#EXTINF`, agrupación por
  `#EXTGRP`/atributo `group-title` o convención equivalente).
- **[VERIFICADO]** 1.041 entradas de radio distribuidas en 24 grupos/categorías.
- **[VERIFICADO]** Tipos de stream presentes en el playlist:
  - MP3 sobre HTTPS
  - AAC sobre HTTPS
  - HLS (`.m3u8`)
  - Icecast genérico (mount points sin extensión fija, cabeceras `icy-*`)
- **[POR VERIFICAR]** Si todas las URLs del playlist están vivas/accesibles en el momento de
  uso (playlists de radio por Internet tienen tasa de caída natural; se recomienda manejo de
  error por entrada, no bloqueo global).
- **[POR VERIFICAR]** Codificación de caracteres del archivo M3U (UTF-8 asumido) y presencia de
  metadatos opcionales (logo por entrada, país, idioma) más allá de nombre y grupo.

## 5. Requisitos funcionales

- **[REQUISITO]** Motor de reproducción de audio (audio engine) bundleado, aislado del sistema
  (no depende de binarios/bibliotecas del firmware salvo ALSA), compilado/empaquetado para
  AArch64, capaz de reproducir MP3, AAC y HLS (segmentado, con descarga y ensamblado de
  segmentos) sobre HTTPS.
- **[REQUISITO]** Pila TLS segura y vigente incluida en el bundle (no depender de la OpenSSL
  rota del sistema); debe validar certificados por defecto.
- **[REQUISITO]** Parser M3U extendido propio (o biblioteca vendorizada), tolerante a entradas
  malformadas individuales sin abortar la carga completa del playlist.
- **[REQUISITO]** Navegación por categorías (los 24 grupos del M3U).
- **[REQUISITO]** Búsqueda/filtro por nombre de emisora — opcional, mejora de UX pero no
  bloqueante para v1.
- **[REQUISITO]** Favoritos persistentes en SD (dentro de `radio/`), sobreviven reinicio de la
  app y del dispositivo.
- **[REQUISITO]** Historial de "recientes" persistente en SD, con límite razonable de entradas.
- **[REQUISITO]** Ciclo de vida limpio del proceso hijo de reproducción: arranque, cambio de
  emisora sin proceso zombie/huérfano, parada limpia al salir de la app, sin fugas de
  descriptores de red al cambiar de canal repetidamente.
- **[REQUISITO]** UI no debe bloquear pantalla ni audio: la interfaz debe permanecer responsive
  (input, navegación, refresco de pantalla) mientras el stream se conecta/buffer/reproduce; sin
  audio glitches perceptibles causados por el hilo de UI.

## 6. Requisitos no funcionales

- **[REQUISITO]** No debe requerir instalación de paquetes ni escritura fuera de la carpeta de
  la app (nada de `apt install`, nada en `/usr`, `/etc`, home del sistema, etc.).
- **[REQUISITO]** Arranque razonablemente rápido en hardware embebido (objetivo orientativo,
  **[POR VERIFICAR]** cifra exacta aceptable en el dispositivo real).
- **[REQUISITO]** Uso de memoria/CPU compatible con hardware de gama baja embebida —
  **[POR VERIFICAR]** límites concretos de RAM/CPU disponibles tras el resto del firmware.
- **[REQUISITO]** Tolerancia a fallos de red: reconexión o mensaje de error claro sin colgar la
  app cuando una emisora no responde o cae a mitad de reproducción.

## 7. Pruebas de aceptación

- **[REQUISITO]** Test de reproducción real de al menos una emisora MP3 sobre HTTPS del
  playlist, con audio audible por ALSA.
- **[REQUISITO]** Test de reproducción real de al menos una emisora AAC sobre HTTPS.
- **[REQUISITO]** Test de reproducción real de al menos un stream HLS (`.m3u8`) con múltiples
  segmentos, incluyendo transición entre segmentos sin corte audible.
- **[REQUISITO]** Test de reproducción de al menos un stream Icecast genérico.
- **[REQUISITO]** Test de cambio rápido entre varias emisoras (stress de ciclo de vida del
  proceso de audio) sin fugas de procesos/descriptores.
- **[REQUISITO]** Test de parseo del M3U completo (1.041 entradas / 24 grupos) verificando
  conteo correcto de entradas y grupos cargados.
- **[REQUISITO]** Test de persistencia: agregar favorito, cerrar app, reabrir, favorito sigue
  presente.
- **[POR VERIFICAR]** Todos los tests de reproducción "real" requieren ejecutarse en el
  dispositivo físico (no simulable fielmente en desarrollo de escritorio) por las diferencias de
  ABI/arquitectura descritas en la sección 3.

## 8. Fuera de alcance (v1)

- Grabación de streams.
- Edición del playlist desde la UI (curación de contenido se hace fuera de la app).
- Ecualizador / efectos de audio.
- Soporte multi-idioma de interfaz (más allá de textos en un único idioma inicial).
