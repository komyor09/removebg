# Remove Background API

Внутренний API-сервис для удаления фона с фотографий (например, фото сотрудников и студентов для
карт). [rembg](https://github.com/danielgatis/rembg) + onnxruntime, только CPU. Только API и Swagger.

```bash
curl -F "file=@photo.jpg" -o photo.png http://<сервер>/rembg/remove-background
curl -F "file=@photo.jpg" -o photo.jpg "http://<сервер>/rembg/remove-background?format=jpeg&bg_color=FFFFFF"
```

Запуск в Docker, переменные окружения, nginx — [DEPLOY.md](DEPLOY.md).

## Локально

```bash
uv sync
mkdir -p models/rembg && curl -L -o models/rembg/u2net_human_seg.onnx \
  https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx
uv run uvicorn app.main:app --port 8001     # Swagger: http://localhost:8001/docs
uv run pytest -q
```

Первый запуск дольше на ~1 мин: numba компилирует зависимость rembg (pymatting) и кеширует результат.

## Структура

```
app/
  main.py      FastAPI: lifespan (загрузка модели один раз), /remove-background, /health
  config.py    настройки из окружения (pydantic-settings)
  images.py    загрузка: лимит размера (413), проверка типа (415), EXIF-поворот
tests/         проверки загрузки и Swagger (модель не нужна)
```
