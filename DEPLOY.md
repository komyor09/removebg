# Деплой: Remove Background API (внутренний сервис)

Только API и Swagger. Удаление фона — [rembg](https://github.com/danielgatis/rembg) +
onnxruntime (CPU). Модель лежит в volume `/models/rembg` и в образ не входит. Изображения не
сохраняются.

## 1. Модель

По умолчанию `u2net_human_seg` (176 МБ, обучена на людях — для фото сотрудников и студентов).
Файл должен называться `<REMBG_MODEL>.onnx`:

```bash
mkdir -p /srv/models/rembg
curl -L -o /srv/models/rembg/u2net_human_seg.onnx \
  https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx
md5sum /srv/models/rembg/u2net_human_seg.onnx   # c09ddc2e0104f800e3e1bb4652583d1f
```

Другие модели — тем же адресом с другим именем (`u2net.onnx`, `u2netp.onnx`, `isnet-general-use.onnx`)
и `REMBG_MODEL=<имя>`. Если файла нет, контейнер не стартует и пишет в лог, чего не хватает
(сам из интернета ничего не качает).

## 2. Сборка и запуск

```bash
cp .env.example .env              # при необходимости поправить
docker build -t removebg .        # ~1 мин уходит на предкомпиляцию numba (один раз)
docker run -d --name removebg -p 8001:8000 \
  -v /srv/models/rembg:/models/rembg:ro --env-file .env removebg
docker logs -f removebg           # "Ready: model u2net_human_seg from /models/rembg"
docker inspect --format '{{.State.Health.Status}}' removebg   # healthy
```

Для локальной проверки без nginx уберите префикс: `-e UVICORN_ROOT_PATH=` (тогда Swagger на
`http://localhost:8001/docs`).

## 3. Переменные окружения

| Переменная | По умолчанию | |
|---|---|---|
| `LOG_LEVEL` | `INFO` | уровень логов (stdout) |
| `MAX_UPLOAD_MB` | `10` | размер файла, больше → `413` |
| `MAX_SIDE` | `2000` | фото больше (px по длинной стороне) уменьшаются перед обработкой |
| `REMBG_MODEL` | `u2net_human_seg` | имя модели rembg |
| `U2NET_HOME` | `/models/rembg` (в образе) | папка с `.onnx` |
| `OMP_NUM_THREADS` | `2` (в образе) | потоков onnxruntime на запрос (= ядра CPU) |
| `ORT_DISABLE_TELEMETRY` | `1` (в образе) | отключает телеметрию onnxruntime: без неё 1.31 пытается слать события в Microsoft |
| `UVICORN_ROOT_PATH` | — | префикс за nginx: `/rembg` → Swagger на `/rembg/docs` |

## 4. Эндпоинты

| Метод | Путь | |
|---|---|---|
| `POST` | `/remove-background` | фото (`file`) → изображение без фона |
| `GET` | `/health` | healthcheck |
| `GET` | `/docs`, `/openapi.json` | Swagger |

Параметры `/remove-background`:
- `format=png` (по умолчанию) — PNG с прозрачным фоном; `format=jpeg` — JPEG с фоном.
- `bg_color=FFFFFF` — цвет фона в HEX (`#` можно не писать). Для JPEG по умолчанию белый.

Форматы входа: JPG, PNG, WEBP, BMP, TIFF (проверяется по содержимому). Ошибки: `400` пустой файл,
`413` больше `MAX_UPLOAD_MB`, `415` не изображение, `422` неверный параметр.

```bash
B=http://localhost:8001            # через nginx: http://<сервер>/rembg
curl $B/health
curl -F "file=@photo.jpg" -o photo.png $B/remove-background
curl -F "file=@photo.jpg" -o photo_white.jpg "$B/remove-background?format=jpeg"
curl -F "file=@photo.jpg" -o photo_blue.jpg "$B/remove-background?format=jpeg&bg_color=1E90FF"
```

## 5. nginx (для общего конфига)

Префикс **срезается** nginx (слеш в конце `proxy_pass`), а `UVICORN_ROOT_PATH=/rembg` сообщает
приложению, где оно опубликовано: так работают и эндпоинты, и Swagger.

```nginx
location /rembg/ {
    proxy_pass http://removebg:8000/;       # слеш в конце: /rembg/remove-background -> /remove-background
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 11m;               # MAX_UPLOAD_MB + запас на multipart
    proxy_read_timeout 60s;
    limit_conn rembg_conn 2;                # не больше 2 одновременных запросов: память
}
```

## 6. Ресурсы

Замеры на 2 vCPU: ~1 с на фото (2000×1500 и 4000×3000 — большие уменьшаются до `MAX_SIDE`).
Память ~0,65 ГБ после загрузки модели, ~0,75 ГБ при трёх запросах одновременно.
Рекомендуемое в compose: `mem_limit: 1g` и `tmpfs: [/tmp]` (FastAPI временно держит загрузки
больше 1 МБ в `/tmp`). Для `limit_conn` в блоке `http` нужна зона:
`limit_conn_zone $server_name zone=rembg_conn:1m;`.

## 7. Тесты

```bash
uv sync && uv run pytest -q        # без модели: проверки 413/415/422 и Swagger
```
