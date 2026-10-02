"""Convert the approved PNG to a multi-resolution ICO; not needed at app runtime."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SIZES = [(size, size) for size in (16, 24, 32, 48, 64, 128, 256)]


def convert(source: Path, output: Path) -> None:
    if source.resolve() == output.resolve():
        raise ValueError("Keep the original PNG; use a separate ICO output path.")
    with Image.open(source) as original:
        image = original.convert("RGBA")
        if image.width != image.height or image.width < 256:
            raise ValueError("Use a square source image at least 256 pixels wide.")
        output.parent.mkdir(parents=True, exist_ok=True)
        frames = [image.resize(size, Image.Resampling.LANCZOS) for size in SIZES]
        frames[-1].save(output, format="ICO", sizes=SIZES, append_images=frames[:-1])
    with Image.open(output) as icon:
        if icon.ico.sizes() != set(SIZES):
            raise RuntimeError("ICO does not contain all seven required sizes.")
        for size in SIZES:
            frame = icon.ico.getimage(size).convert("RGBA")
            if frame.getchannel("A").getextrema() != (0, 255):
                raise RuntimeError(f"The {size} icon must retain transparent and opaque pixels.")
    print(f"Created {output} with seven RGBA sizes: {SIZES}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "assets/icons/app.png")
    parser.add_argument("--output", type=Path, default=ROOT / "assets/icons/app.ico")
    args = parser.parse_args()
    convert(args.source, args.output)
