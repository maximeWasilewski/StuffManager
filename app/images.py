"""Photo files, Code 128 barcodes, and QR codes."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import qrcode
from barcode import Code128
from barcode.writer import ImageWriter
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

# Pillow does not read iPhone HEIC on its own. The opener uses libheif,
# shipped inside the pillow-heif wheel (amd64 and aarch64).
register_heif_opener()

MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_EDGE = 1600

# Some JPEG exports carry extra pictures (depth/HDR) and Pillow reports MPO.
# Convert their primary image, just as for HEIF, to the stored JPEG.
_ALLOWED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP", "HEIF", "AVIF", "TIFF"}
_FORMAT_MESSAGE = "Utilisez un JPEG (y compris MPO), PNG, WebP, HEIC, AVIF ou TIFF."


class PhotoError(ValueError):
    """A photo the user sent cannot be stored. The message is safe to show."""


def photo_file(photos_dir: Path, item_id: int) -> Path:
    return photos_dir / f"{item_id}.jpg"


def qr_payload(item_id: int) -> str:
    """Relative fiche path. The phone must already be on this server to open it."""
    return f"/composants/{item_id}"


def process_photo(data: bytes) -> bytes:
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoError("La photo dépasse 8 Mo.")
    try:
        image = Image.open(BytesIO(data))
        source_format = image.format
        if source_format not in _ALLOWED_FORMATS:
            raise PhotoError("Ce format de photo n'est pas pris en charge. " + _FORMAT_MESSAGE)
        image.load()
    except Image.DecompressionBombError as exc:
        raise PhotoError("La photo est trop grande en pixels.") from exc
    except PhotoError:
        raise
    except Exception as exc:
        raise PhotoError(
            "Fichier image illisible. " + _FORMAT_MESSAGE
        ) from exc
    image = ImageOps.exif_transpose(image) or image
    image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    rgb = _to_rgb(image)
    output = BytesIO()
    rgb.save(output, format="JPEG", quality=85, optimize=True)
    return output.getvalue()


def _to_rgb(image: Image.Image) -> Image.Image:
    has_alpha = "A" in image.getbands() or (
        image.mode == "P" and "transparency" in image.info
    )
    if not has_alpha:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    background = Image.new("RGB", rgba.size, (255, 255, 255))
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


def write_photo(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def remove_photo(path: Path) -> None:
    path.unlink(missing_ok=True)


def code128_png(code: str) -> bytes:
    buffer = BytesIO()
    writer = ImageWriter()
    Code128(code, writer=writer).write(
        buffer,
        options={
            "write_text": False,
            "module_width": 0.4,
            "module_height": 15.0,
            "quiet_zone": 3.0,
            "dpi": 200,
        },
    )
    return buffer.getvalue()


def qr_png(payload: str) -> bytes:
    code = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    code.add_data(payload)
    code.make(fit=True)
    image = code.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
