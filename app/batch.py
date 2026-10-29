"""Batch input: several files (a folder) and/or ZIP archives -> one ZIP with the results.

Nothing is read into memory up front: first a cheap plan (names, counts, sizes) is checked against
the limits, then images are read, processed and written to the output ZIP one at a time.
"""

import logging
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from fastapi import HTTPException, UploadFile

from app.images import decode_image

log = logging.getLogger("removebg")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
ZIP_TYPES = {"application/zip", "application/x-zip-compressed", "application/x-zip"}
JUNK_NAMES = {"thumbs.db", "desktop.ini"}  # plus anything hidden (".DS_Store") and __MACOSX/
ERRORS_FILE = "_errors.txt"


@dataclass
class Source:
    """One image to process: where it goes in the output and how to read it."""

    name: str  # relative path inside the result, e.g. "group-1/ivanov.jpg"
    size: int
    read: Callable[[], bytes]


def clean_name(raw: str) -> str | None:
    """'C:\\a\\..\\b/x.jpg' -> 'b/x.jpg'. No absolute paths, no '..' (protects against zip-slip)."""
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".", "..") and not p.endswith(":")]
    return "/".join(parts) or None


def is_junk(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return any(p.startswith(".") or p == "__MACOSX" for p in parts) or parts[-1].lower() in JUNK_NAMES


def zip_entry_name(info: zipfile.ZipInfo) -> str:
    """Windows Explorer writes Cyrillic names in cp866 without the UTF-8 flag; Python decodes them as cp437."""
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp866")


def is_zip(upload: UploadFile) -> bool:
    if (upload.filename or "").lower().endswith(".zip") or upload.content_type in ZIP_TYPES:
        return True
    upload.file.seek(0)
    magic = upload.file.read(4)
    upload.file.seek(0)
    return magic == b"PK\x03\x04"


def _reader(fileobj: BinaryIO, limit: int, label: str) -> Callable[[], bytes]:
    def read() -> bytes:
        fileobj.seek(0)
        data = fileobj.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"{label}: файл больше {limit // 1024 // 1024} МБ")
        return data

    return read


def _zip_reader(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> Callable[[], bytes]:
    def read() -> bytes:
        with zf.open(info) as f:  # read() with a limit: file_size in the header can lie (zip bomb)
            data = f.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"файл больше {limit // 1024 // 1024} МБ")
        return data

    return read


def plan(uploads: list[UploadFile], max_file_mb: int, max_files: int, max_total_mb: int):
    """Uploads -> (images to process, skipped names, open zip files to close afterwards).

    Raises 400 (broken ZIP / nothing to process) and 413 (over the limits) before any heavy work.
    """
    file_limit = max_file_mb * 1024 * 1024
    total = sum(u.size or 0 for u in uploads)
    if total > max_total_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"Всего загружено больше {max_total_mb} МБ")

    sources: list[Source] = []
    skipped: list[str] = []
    zips: list[zipfile.ZipFile] = []

    def add(name: str | None, size: int, read: Callable[[], bytes], prefix: str = "") -> None:
        if not name or is_junk(name):
            return
        name = f"{prefix}/{name}" if prefix else name
        if Path(name).suffix.lower() not in IMAGE_EXTS:
            skipped.append(f"{name}: пропущен — не изображение")
            return
        if size > file_limit:
            skipped.append(f"{name}: пропущен — больше {max_file_mb} МБ")
            return
        sources.append(Source(name, size, read))

    many_zips = sum(is_zip(u) for u in uploads) > 1
    for upload in uploads:
        label = upload.filename or "file"
        if not is_zip(upload):
            add(clean_name(label), upload.size or 0, _reader(upload.file, file_limit, label))
            continue
        try:
            zf = zipfile.ZipFile(upload.file)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail=f"{label}: повреждённый ZIP-архив") from None
        zips.append(zf)
        # Several archives: each one gets its own folder, so equal names inside them do not collide
        prefix = (clean_name(Path(label).stem) or "archive") if many_zips else ""
        for info in zf.infolist():
            if not info.is_dir():
                add(clean_name(zip_entry_name(info)), info.file_size, _zip_reader(zf, info, file_limit), prefix)

    if len(sources) > max_files:
        for zf in zips:
            zf.close()
        raise HTTPException(status_code=413, detail=f"Изображений {len(sources)}, максимум {max_files} за раз")
    if not sources:
        for zf in zips:
            zf.close()
        raise HTTPException(status_code=400, detail="Не найдено ни одного изображения (JPG, PNG, WEBP, BMP, TIFF)")
    return sources, skipped, zips


def output_name(name: str, fmt: str, used: set[str]) -> str:
    """'a/photo.JPG' -> 'a/photo.png'; 'photo.jpg' and 'photo.png' in one folder -> 'photo.png', 'photo_2.png'."""
    path = PurePosixPath(name).with_suffix(".jpg" if fmt == "jpeg" else ".png")
    candidate, n = str(path), 1
    while candidate.lower() in used:
        n += 1
        candidate = str(path.with_name(f"{path.stem}_{n}{path.suffix}"))
    used.add(candidate.lower())
    return candidate


def process(
    sources: list[Source],
    skipped: list[str],
    out: BinaryIO,
    fmt: str,
    remove_bg: Callable,
) -> tuple[int, list[str]]:
    """Process images one by one into the output ZIP. A bad image does not stop the batch: it goes to _errors.txt.

    remove_bg(image, fmt) -> bytes. Returns (processed count, error lines). Runs in a worker thread.
    """
    errors = list(skipped)
    done = 0
    used: set[str] = set()
    # Images are already compressed: ZIP_STORED is as small and much faster
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as result:
        for src in sources:
            try:
                image = decode_image(src.read(), src.name)
                result.writestr(output_name(src.name, fmt, used), remove_bg(image, fmt))
                done += 1
            except HTTPException as e:
                errors.append(e.detail if e.detail.startswith(src.name) else f"{src.name}: {e.detail}")
            except Exception:
                log.exception("Batch: failed on %s", src.name)
                errors.append(f"{src.name}: ошибка обработки")
        if errors:
            result.writestr(ERRORS_FILE, "\n".join(errors) + "\n")
    return done, errors
