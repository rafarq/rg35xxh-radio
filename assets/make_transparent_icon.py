import argparse
from collections import deque
from pathlib import Path

from PIL import Image

DEFAULT_OUTPUT = Path(__file__).resolve().with_name("Radio-transparent.png")


def make_transparent_icon(source: Path, target: Path) -> None:
    image = Image.open(source).convert("RGBA")
    pixels = image.load()
    width, height = image.size
    visited = bytearray(width * height)
    background = bytearray(width * height)

    def is_preview_background(x: int, y: int) -> bool:
        red, green, blue, alpha = pixels[x, y]
        return bool(alpha) and min(red, green, blue) >= 205 and max(red, green, blue) - min(red, green, blue) <= 24

    queue = deque()
    for x in range(width):
        for y in (0, height - 1):
            if is_preview_background(x, y):
                queue.append((x, y))
    for y in range(height):
        for x in (0, width - 1):
            if is_preview_background(x, y):
                queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        index = y * width + x
        if visited[index] or not is_preview_background(x, y):
            continue
        visited[index] = 1
        background[index] = 1
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height and not visited[ny * width + nx]:
                queue.append((nx, ny))

    alpha = Image.new("L", image.size, 0)
    alpha_pixels = alpha.load()
    for y in range(height):
        for x in range(width):
            if not background[y * width + x]:
                alpha_pixels[x, y] = 255

    bbox = alpha.getbbox()
    foreground = image.copy()
    foreground.putalpha(alpha)
    foreground = foreground.crop(bbox)
    foreground.thumbnail((172, 138), Image.Resampling.LANCZOS)
    icon = Image.new("RGBA", (240, 180), (0, 0, 0, 0))
    icon.alpha_composite(foreground, ((240 - foreground.width) // 2, (180 - foreground.height) // 2))
    icon.save(target, optimize=True)
    print(f"source={image.size}; foreground={bbox}; artwork={foreground.size}; target={icon.size}; alpha_bbox={icon.getchannel('A').getbbox()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove the connected light preview background from an image.")
    parser.add_argument("source", type=Path, help="source image path")
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT, help=f"output PNG path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()
    make_transparent_icon(args.source, args.output)


if __name__ == "__main__":
    main()
