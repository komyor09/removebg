# Remove Background API — internal API service (rembg + onnxruntime, CPU only)
#
#   docker build -t removebg .
#   docker run -d -p 8001:8000 -v /srv/models/rembg:/models/rembg:ro --env-file .env removebg
#
# The model (.onnx) is NOT in the image: it is read from the /models/rembg volume (see DEPLOY.md).

# ---------- stage 1: Python dependencies ----------
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
# requirements.txt first: this layer is rebuilt only when dependencies change, not on code edits
COPY requirements.txt .
RUN /opt/venv/bin/pip install -r requirements.txt


# ---------- stage 2: runtime ----------
FROM python:3.12-slim

# NUMBA_CACHE_DIR: rembg imports pymatting, which compiles itself with numba on first import
#   (~50 s on 2 vCPU). The compiled cache is made once at build time and stays writable for the app.
# MALLOC_ARENA_MAX=2: fewer glibc memory arenas -> noticeably lower RSS on a small server.
# OMP_NUM_THREADS: onnxruntime threads per request (= CPU cores).
# ORT_DISABLE_TELEMETRY=1: onnxruntime 1.31 otherwise tries to send usage telemetry to Microsoft.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NUMBA_CACHE_DIR=/opt/numba-cache \
    MALLOC_ARENA_MAX=2 \
    OMP_NUM_THREADS=2 \
    ORT_DISABLE_TELEMETRY=1 \
    U2NET_HOME=/models/rembg

RUN useradd --system --uid 10001 --no-create-home app

COPY --from=build /opt/venv /opt/venv
RUN python -c "import rembg" \
 && chown -R app /opt/numba-cache

WORKDIR /app
COPY app ./app

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
