# Shared playlists

This folder is the user-shareable playlist source area. Copy an Extended M3U
file (`.m3u` or `.m3u8`, case-insensitive) directly into this folder, then
open **Settings → Playlists** in Radio and select its filename.

For remote lists, add one `http://` or `https://` URL per line to
`playlist_urls.txt` in this same folder. Blank lines and lines beginning with
`#` are ignored. Only HTTP(S) URLs are supported; other schemes and invalid
URLs are ignored. HTTPS certificate verification remains enabled.

The app downloads at most 2 MiB per remote playlist. A valid remote download
is cached. If a later download fails, Radio uses that cached copy; if there is
no usable cache (or a local playlist is missing or invalid), it safely falls
back to the bundled playlist.

To share playlists with another device, copy the entire `App` folder rather
than just this directory. This preserves Radio, its bundled CA certificate,
and the expected relative paths; the recipient can then replace or add files
in `radio/playlists/` as needed.
