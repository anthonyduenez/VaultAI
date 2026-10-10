"""Build the HEIC/AVIF test fixtures with pypdfium2, Pillow, and macOS sips.

Usage: backend/.venv/bin/python make_fixtures.py <repo> <workdir>
"""

import subprocess
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

repo, work = Path(sys.argv[1]), Path(sys.argv[2])
out = repo / "backend/tests/core/fixtures"
out.mkdir(parents=True, exist_ok=True)
work.mkdir(parents=True, exist_ok=True)

page = pdfium.PdfDocument(repo / "samples/scanner/receipt_harbor_fuel.pdf")[0]
receipt = page.render(scale=200 / 72).to_pil().convert("RGB")
margin = 60
photo = Image.new("RGB", (receipt.width + 2 * margin, receipt.height + 2 * margin), (128, 128, 128))
photo.paste(receipt, (margin, margin))
print("receipt photo size", photo.size)

upright = work / "receipt_upright.jpg"
photo.save(upright, quality=90)

sideways = work / "receipt_sideways.jpg"
exif = Image.Exif()
exif[0x0112] = 6
photo.transpose(Image.Transpose.ROTATE_90).save(sideways, quality=90, exif=exif.tobytes())

huge = work / "huge.jpg"
Image.new("RGB", (8064, 6048), (250, 250, 250)).save(huge, quality=60)


def sips(src: Path, fmt: str, dest: Path) -> None:
    subprocess.run(["sips", "-s", "format", fmt, str(src), "--out", str(dest)], check=True, capture_output=True)
    print(dest.name, dest.stat().st_size, "bytes")


sips(upright, "heic", out / "receipt_harbor_fuel.heic")
sips(sideways, "heic", out / "receipt_harbor_fuel_rotated.heic")
sips(huge, "heic", out / "blank_8064x6048.heic")
tiny = work / "tiny.png"
Image.new("RGB", (64, 64), (200, 200, 200)).save(tiny)
sips(tiny, "avif", out / "gray_64x64.avif")
