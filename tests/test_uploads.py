"""Upload checks (413, 415, 422) work without the model: they happen before any processing."""

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)  # no `with`: lifespan (model loading) is not started


def test_wrong_type_is_415():
    r = client.post("/remove-background", files={"file": ("doc.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_not_an_image_is_415():
    r = client.post("/remove-background", files={"file": ("fake.png", b"not a png", "image/png")})
    assert r.status_code == 415


def test_too_big_is_413():
    big = b"0" * (get_settings().max_upload_mb * 1024 * 1024 + 1)
    r = client.post("/remove-background", files={"file": ("big.jpg", big, "image/jpeg")})
    assert r.status_code == 413


def test_bad_color_is_422():
    r = client.post("/remove-background?bg_color=red", files={"file": ("a.png", b"x", "image/png")})
    assert r.status_code == 422


def test_swagger_is_served():
    assert client.get("/docs").status_code == 200
    assert "/remove-background" in client.get("/openapi.json").json()["paths"]
