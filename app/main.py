"""Background removal API (rembg, CPU only). API + Swagger only."""

import io
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from onnxruntime import SessionOptions
from PIL import Image
from pydantic import BaseModel
from rembg import new_session, remove

from app.config import get_settings
from app.images import decode_image, read_upload

settings = get_settings()
log = logging.getLogger("removebg")


def setup_logging(level: str) -> None:
    """All logs (ours and uvicorn's) go to stdout in one format."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(level.upper())
    # Docker calls /health every 30 s: keep it out of the access log
    logging.getLogger("uvicorn.access").addFilter(lambda record: "/health" not in record.getMessage())


setup_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The model is loaded once here and shared by all requests. Fails fast if the file is missing,
    # instead of silently trying to download it from the internet.
    models = Path(settings.u2net_home).resolve()
    model_file = models / f"{settings.rembg_model}.onnx"
    if not model_file.is_file():
        raise RuntimeError(f"Нет модели {model_file}. Скачайте её в U2NET_HOME (см. DEPLOY.md)")
    os.environ["U2NET_HOME"] = str(models)  # rembg looks for the model here
    opts = SessionOptions()
    # Without the memory arena onnxruntime returns memory after each request:
    # ~0.6 GB steady instead of ~1.2 GB (measured with u2net_human_seg)
    opts.enable_cpu_mem_arena = False
    app.state.session = await run_in_threadpool(new_session, settings.rembg_model, sess_opts=opts)
    log.info("Ready: model %s from %s", settings.rembg_model, models)
    yield


app = FastAPI(
    title="Remove Background API",
    version="0.1.0",
    description="Удаление фона с фотографии (rembg, CPU). Изображения не сохраняются.",
    lifespan=lifespan,
)


def _remove_background(session, image: Image.Image, fmt: str, bg: tuple[int, int, int] | None) -> bytes:
    """CPU-heavy part: the neural network + encoding the result. Runs in a worker thread."""
    image.thumbnail((settings.max_side, settings.max_side))  # keeps proportions; no-op for small images
    if fmt == "jpeg" and bg is None:
        bg = (255, 255, 255)  # JPEG has no transparency: white by default
    result = remove(image, session=session, bgcolor=(*bg, 255) if bg else None)
    buf = io.BytesIO()
    if fmt == "jpeg":
        result.convert("RGB").save(buf, format="JPEG", quality=95)
    else:
        result.save(buf, format="PNG")
    return buf.getvalue()


@app.post(
    "/remove-background",
    summary="Удалить фон с изображения",
    response_class=Response,
    responses={
        200: {"content": {"image/png": {}, "image/jpeg": {}}, "description": "Изображение без фона"},
        413: {"description": "Файл больше MAX_UPLOAD_MB"},
        415: {"description": "Не изображение (JPG, PNG, WEBP, BMP, TIFF)"},
    },
)
async def remove_background(
    request: Request,
    file: UploadFile = File(..., description="Фотография: JPG, PNG, WEBP, BMP, TIFF"),
    format: Literal["png", "jpeg"] = Query(
        "png", description="png — с прозрачным фоном (или bg_color), jpeg — с фоном bg_color (белый)"
    ),
    bg_color: str | None = Query(
        None, pattern=r"^#?[0-9A-Fa-f]{6}$", description="Цвет фона в HEX, например FFFFFF или #1E90FF"
    ),
):
    data = await read_upload(file, settings.max_upload_mb)
    image = await run_in_threadpool(decode_image, data, file.filename)
    bg = tuple(int(bg_color.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)) if bg_color else None
    content = await run_in_threadpool(_remove_background, request.app.state.session, image, format, bg)
    return Response(content=content, media_type=f"image/{format}")


class HealthResponse(BaseModel):
    status: str
    model: str


@app.get("/health", response_model=HealthResponse, tags=["service"], summary="Проверка работоспособности")
def health():
    return HealthResponse(status="ok", model=settings.rembg_model)
