"""Reading an uploaded image: size limit (413), type check (415), decoding."""

import io

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps

# The format is checked by the file content: some clients send images as application/octet-stream
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "TIFF"}
GENERIC_TYPES = {None, "", "application/octet-stream"}
MAX_PIXELS = 40_000_000  # ~ 7300x5500; protects the small server from "decompression bomb" images
CHUNK = 1024 * 1024

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


async def read_upload(file: UploadFile, max_mb: int) -> bytes:
    """Read the file in chunks and stop as soon as it is over the limit."""
    declared = file.content_type
    if declared not in GENERIC_TYPES and not declared.startswith("image/"):
        raise HTTPException(status_code=415, detail=f"{file.filename}: тип {declared!r} — не изображение")
    limit = max_mb * 1024 * 1024
    data = bytearray()
    while chunk := await file.read(CHUNK):
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"{file.filename}: файл больше {max_mb} МБ")
    if not data:
        raise HTTPException(status_code=400, detail=f"{file.filename}: пустой файл")
    return bytes(data)


def decode_image(data: bytes, filename: str | None) -> Image.Image:
    """Bytes -> RGB image with the phone's EXIF rotation applied."""
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ALLOWED_FORMATS:
            raise HTTPException(
                status_code=415,
                detail=f"{filename}: формат {img.format} не поддерживается; "
                f"допустимо: {', '.join(sorted(ALLOWED_FORMATS))}",
            )
        return ImageOps.exif_transpose(img).convert("RGB")
    except Image.DecompressionBombError:
        raise HTTPException(status_code=413, detail=f"{filename}: больше {MAX_PIXELS:,} пикселей") from None
    except OSError:  # includes UnidentifiedImageError
        raise HTTPException(status_code=415, detail=f"{filename}: файл не читается как изображение") from None
