# RG35XX H Radio

![RG35XX H Radio logo](assets/Radio-transparent.png)

Aplicación de radio por Internet para la consola **Anbernic RG35XX H** con
**MuOS**. Navega una lista M3U incluida, reproduce emisoras y conserva
favoritos, recientes y la configuración en la tarjeta SD.

## Instalación en MuOS

Copie los archivos del proyecto a la tarjeta SD respetando esta estructura:

```text
/mnt/mmc/Roms/APPS/Radio.sh
/mnt/mmc/Roms/APPS/radio/
```

En otras palabras, copie `Radio.sh` a `Roms/APPS/` y copie el directorio
`radio/` completo a `Roms/APPS/radio/`. Para el icono del menú, copie el PNG
correspondiente como `Roms/APPS/Imgs/Radio.png`. La aplicación se ejecuta desde
el menú de APPS de MuOS.

El paquete de instalación debe incluir también el motor de audio AArch64 en
`radio/engine/ffmpeg-aarch64`; ese binario no está en este repositorio. Consulte
[Arquitectura](#arquitectura) y [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Controles

| Control | Acción |
| --- | --- |
| D-pad | Mover el cursor o la selección |
| A | Abrir, seleccionar o reproducir; reintentar tras un error |
| B | Volver; en reproducción, detener y volver |
| SELECT | Volver a Inicio |
| X | Reproducir desde una lista; pausar o reanudar durante la escucha |
| START | Añadir o quitar favoritos |
| L1 / R1 | Categoría anterior / siguiente |
| VOL− / VOL+ | Ajustar el volumen del mezclador ALSA |
| MENU | Salir limpiamente |

## Emisoras y listas

La lista incluida contiene emisoras MP3 y AAC, streams HLS (`.m3u8`) y streams
Icecast genéricos. La disponibilidad de una emisora concreta depende de su
servidor remoto.

Puede añadir listas Extended M3U locales (`.m3u` o `.m3u8`) en:

```text
/mnt/mmc/Roms/APPS/radio/playlists/
```

Para listas remotas, añada una URL completa `http://` o `https://` por línea en:

```text
/mnt/mmc/Roms/APPS/radio/playlists/playlist_urls.txt
```

Se permiten líneas vacías y comentarios que comiencen por `#`; otros esquemas
se ignoran. Después, en la consola abra **Settings → Playlists**, elija el
archivo local o la URL y confirme con **A**. La selección se guarda en la
configuración de la aplicación.

Las descargas remotas se realizan con verificación normal de certificado y
hostname HTTPS y tienen un límite de 2 MiB. Cada descarga válida se guarda en
caché; si la actualización posterior falla, se usa esa copia. Sin una caché
válida —o si una lista local no existe, está vacía o no es válida— Radio vuelve
de forma segura a la lista incluida.

## Arquitectura

La interfaz está escrita en Python y usa SDL2/Pillow en el dispositivo. El
parser M3U, la gestión de listas, el estado de la app y la persistencia están
separados de la capa de interfaz para poder probarlos en escritorio.

La reproducción conecta un decodificador `ffmpeg-aarch64` estático con
`/usr/bin/aplay`: FFmpeg convierte el stream en PCM y `aplay` lo entrega a
ALSA. El bundle de CA `radio/assets/cacert.pem` se asigna mediante
`SSL_CERT_FILE` al decodificador para verificar HTTPS sin depender del almacén
TLS del firmware.

El binario `radio/engine/ffmpeg-aarch64` se excluye intencionadamente de Git y
debe obtenerse e incorporarse por separado al artefacto de instalación. Al
distribuirlo, deben cumplirse las obligaciones de licencia y disponibilidad de
código fuente de la compilación concreta; vea
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) y
[radio/engine/README.md](radio/engine/README.md).

## Desarrollo y pruebas

Se requiere Python 3.10 o posterior. Desde la raíz del repositorio:

```sh
uv run --with pytest python -m pytest -q
uv run python -m radio.smoke
```

La interfaz completa requiere el hardware/firmware objetivo, PySDL2, Pillow,
ALSA, `/dev/input/event1` y el binario externo de FFmpeg. Las pruebas y el
smoke test no requieren SDL2 ni una pantalla.

## Límites de seguridad

Radio solo acepta URLs de listas HTTP(S), conserva la verificación HTTPS y no
ejecuta listas ni URLs mediante una shell. Las entradas M3U se tratan como datos
no confiables y las entradas malformadas se omiten. La caché y la persistencia
se mantienen dentro del árbol de la aplicación en la SD. Estas medidas no hacen
confiables a los streams de terceros: use únicamente listas y emisoras en las
que confíe.

Las vulnerabilidades deben comunicarse de forma responsable según
[SECURITY.md](SECURITY.md).

## English quick start

Copy `Radio.sh` to `Roms/APPS/` and the complete `radio/` directory to
`Roms/APPS/radio/` on the MuOS SD card. Supply the separately obtained
`radio/engine/ffmpeg-aarch64` binary. Local M3U files go in
`radio/playlists/`; remote HTTP(S) URLs go one per line in
`radio/playlists/playlist_urls.txt`; select them in **Settings → Playlists**.

## Créditos / Autor

Rafael Roa

Technical Architect & CTO building where architecture, code and AI meet.

- Website: https://rafarq.com
- GitHub: https://github.com/rafarq
- LinkedIn: https://www.linkedin.com/in/rafaroa
- Instagram: https://www.instagram.com/r4f4r04
- Threads: https://www.threads.net/@r4f4r04
- Mastodon: https://mastodon.cloud/@rafarq

Licencia: [MIT](LICENSE).
