from __future__ import annotations

import base64
import io
import re
from typing import Optional

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover - optional dependency fallback
    Image = None
    ImageOps = None

MAX_PROFILE_PHOTO_UPLOAD_BYTES = 13 * 1024 * 1024
MAX_PROFILE_PHOTO_DATA_URL_LENGTH = 25_000_000
_DATA_IMAGE_RE = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", re.DOTALL)


def _encode_data_url(mime: str, payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def normalize_profile_photo_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    if len(trimmed) > MAX_PROFILE_PHOTO_DATA_URL_LENGTH:
        raise ValueError("PROFILE_PHOTO_TOO_LARGE")

    match = _DATA_IMAGE_RE.match(trimmed)
    if not match:
        return trimmed

    encoded_payload = match.group(2)
    try:
        binary = base64.b64decode(encoded_payload, validate=True)
    except Exception as exc:
        raise ValueError("PROFILE_PHOTO_INVALID_DATA") from exc

    if len(binary) > MAX_PROFILE_PHOTO_UPLOAD_BYTES:
        raise ValueError("PROFILE_PHOTO_TOO_LARGE")

    if Image is None or ImageOps is None:
        return trimmed

    try:
        image = Image.open(io.BytesIO(binary))
        image = ImageOps.exif_transpose(image)
    except Exception as exc:
        raise ValueError("PROFILE_PHOTO_INVALID_IMAGE") from exc

    width, height = image.size
    if width > 1280 or height > 1280:
        resampling = (
            Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        )
        image.thumbnail((1280, 1280), resampling)

    original_format = (image.format or "").upper()
    has_alpha = "A" in image.getbands()
    output = io.BytesIO()

    if original_format == "PNG":
        image.save(output, format="PNG", optimize=True, compress_level=9)
        result_mime = "image/png"
    elif original_format == "WEBP":
        image.save(output, format="WEBP", quality=80, method=6)
        result_mime = "image/webp"
    else:
        if image.mode not in {"RGB", "L"}:
            if has_alpha:
                base = Image.new("RGB", image.size, (255, 255, 255))
                alpha = image.getchannel("A")
                base.paste(image.convert("RGB"), mask=alpha)
                image = base
            else:
                image = image.convert("RGB")
        image.save(output, format="JPEG", quality=82, optimize=True, progressive=True)
        result_mime = "image/jpeg"

    compressed = output.getvalue()
    if compressed and len(compressed) <= len(binary):
        return _encode_data_url(result_mime, compressed)
    return trimmed
