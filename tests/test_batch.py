"""/remove-background/batch: folder (several files) and ZIP. The model is replaced with a stub."""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main
from app.batch import clean_name

client = TestClient(main.app)  # no `with`: lifespan (model loading) is not started
URL = "/remove-background/batch"


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    main.app.state.session = None
    monkeypatch.setattr(main, "_remove_background", lambda session, img, fmt, bg: f"{fmt}:{img.size}".encode())


def png(size=(4, 3)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, format="PNG")
    return buf.getvalue()


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def result(r) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def test_zip_keeps_folders_and_skips_junk():
    z = make_zip({
        "group-1/a.jpg": png(),
        "group-1/sub/b.PNG": png(),
        "group-1/readme.txt": b"hi",
        "__MACOSX/group-1/._a.jpg": b"x",
        "group-1/.DS_Store": b"x",
    })
    r = client.post(URL, files={"files": ("photos.zip", z, "application/zip")})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "photos_nobg.zip" in r.headers["content-disposition"]
    assert (r.headers["x-processed"], r.headers["x-failed"]) == ("2", "1")
    out = result(r)
    assert set(out) == {"group-1/a.png", "group-1/sub/b.png", "_errors.txt"}
    assert b"readme.txt" in out["_errors.txt"]


def test_folder_as_several_files_jpeg():
    files = [
        ("files", ("dir/a.jpg", png(), "image/jpeg")),
        ("files", ("dir/a.png", png(), "image/png")),  # same stem -> a_2.jpg
    ]
    r = client.post(URL + "?format=jpeg", files=files)
    assert r.status_code == 200
    assert set(result(r)) == {"dir/a.jpg", "dir/a_2.jpg"}
    assert r.headers["x-failed"] == "0"


def test_bad_image_goes_to_errors_txt():
    z = make_zip({"ok.png": png(), "broken.jpg": b"not an image"})
    r = client.post(URL, files={"files": ("p.zip", z, "application/zip")})
    assert r.status_code == 200
    out = result(r)
    assert "ok.png" in out and b"broken.jpg" in out["_errors.txt"]


def test_all_failed_is_422():
    z = make_zip({"broken.jpg": b"nope"})
    r = client.post(URL, files={"files": ("p.zip", z, "application/zip")})
    assert r.status_code == 422
    assert "broken.jpg" in r.json()["detail"][0]


def test_no_images_is_400():
    z = make_zip({"a.txt": b"x"})
    assert client.post(URL, files={"files": ("p.zip", z, "application/zip")}).status_code == 400


def test_broken_zip_is_400():
    assert client.post(URL, files={"files": ("p.zip", b"PK\x03\x04garbage", "application/zip")}).status_code == 400


def test_too_many_files_is_413(monkeypatch):
    monkeypatch.setattr(main.settings, "max_batch_files", 2)
    z = make_zip({f"{i}.png": png() for i in range(3)})
    assert client.post(URL, files={"files": ("p.zip", z, "application/zip")}).status_code == 413


def test_two_zips_get_own_folders():
    files = [
        ("files", ("x.zip", make_zip({"a.png": png()}), "application/zip")),
        ("files", ("y.zip", make_zip({"a.png": png()}), "application/zip")),
    ]
    r = client.post(URL, files=files)
    assert set(result(r)) == {"x/a.png", "y/a.png"}


class Cp866Info(zipfile.ZipInfo):
    """How Windows Explorer stores Cyrillic names: cp866 bytes, no UTF-8 flag."""

    def _encodeFilenameFlags(self):
        return self.filename.encode("cp866"), self.flag_bits & ~0x800


def test_cyrillic_names_from_windows_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = Cp866Info("Иванов.png")
        zf.writestr(info, png())
    r = client.post(URL, files={"files": ("p.zip", buf.getvalue(), "application/zip")})
    assert set(result(r)) == {"Иванов.png"}


def test_zip_slip_names_are_cleaned():
    assert clean_name("../../etc/x.png") == "etc/x.png"
    assert clean_name("C:\\Users\\a\\..\\b.jpg") == "Users/a/b.jpg"
    assert clean_name("/abs/./c.png") == "abs/c.png"
