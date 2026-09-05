"""
Tests for doctor profile photo upload/replace/remove
(app/api/doctor_photo.py): validation (format, size, dimensions),
storage (a new file per upload, the old one deleted on replace/remove),
and the resulting doctors.photo_url reference.

Storage is local disk under app.config.MEDIA_ROOT (see that module's
docstring for why) -- these tests check the file actually appears/
disappears on disk, not just the API response, since "the old photo is
deleted on replace" is exactly the kind of thing that's easy to get
half-right (delete-before-save loses the old file if the new save then
fails; the tests below don't re-verify failure ordering, just the
end-to-end outcome).
"""

import io
from pathlib import Path

from PIL import Image

from app.config import MEDIA_ROOT
from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers


def _jpeg_bytes(size=(100, 100), color=(255, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _png_bytes(size=(100, 100)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color=(0, 255, 0, 128)).save(buf, format="PNG")
    return buf.getvalue()


def _create_doctor(client, admin_headers, name="Dr. Photo Test"):
    return client.post(
        "/api/doctors",
        json={"name": name, "specialization": "Cardiology"},
        headers=admin_headers,
    ).json()


def _photo_path_on_disk(photo_url: str) -> Path:
    return MEDIA_ROOT / "doctors" / Path(photo_url).name


def test_upload_valid_jpeg_photo(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers)

    response = client.post(
        f"/api/doctors/{doctor['id']}/photo",
        files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")},
        headers=admin_headers,
    )

    assert response.status_code == 200
    photo_url = response.json()["photo_url"]
    assert photo_url.startswith("/media/doctors/")
    assert _photo_path_on_disk(photo_url).is_file()

    profile = client.get(f"/api/doctors/{doctor['id']}").json()
    assert profile["photo_url"] == photo_url


def test_upload_valid_png_photo(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers)

    response = client.post(
        f"/api/doctors/{doctor['id']}/photo",
        files={"file": ("photo.png", _png_bytes(), "image/png")},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert _photo_path_on_disk(response.json()["photo_url"]).is_file()


def test_upload_rejects_non_image_file(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers)

    response = client.post(
        f"/api/doctors/{doctor['id']}/photo",
        files={"file": ("not_a_photo.txt", b"just some text, not an image", "text/plain")},
        headers=admin_headers,
    )

    assert response.status_code == 400


def test_upload_rejects_oversized_file(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers)

    # A real (small) JPEG padded with a huge comment/junk trailer so the
    # *file* is oversized without needing to actually encode a giant
    # image -- the endpoint's own size check happens before any decode.
    oversized = _jpeg_bytes() + b"\x00" * (2 * 1024 * 1024 + 1)

    response = client.post(
        f"/api/doctors/{doctor['id']}/photo",
        files={"file": ("huge.jpg", oversized, "image/jpeg")},
        headers=admin_headers,
    )

    assert response.status_code == 400


def test_upload_rejects_dimensions_too_large(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers)

    huge_dimension_jpeg = _jpeg_bytes(size=(6001, 10))

    response = client.post(
        f"/api/doctors/{doctor['id']}/photo",
        files={"file": ("wide.jpg", huge_dimension_jpeg, "image/jpeg")},
        headers=admin_headers,
    )

    assert response.status_code == 400


def test_upload_downscales_large_but_valid_image(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers)

    large_valid = _jpeg_bytes(size=(2000, 1500))

    response = client.post(
        f"/api/doctors/{doctor['id']}/photo",
        files={"file": ("big.jpg", large_valid, "image/jpeg")},
        headers=admin_headers,
    )

    assert response.status_code == 200
    stored_path = _photo_path_on_disk(response.json()["photo_url"])
    with Image.open(stored_path) as stored:
        assert max(stored.size) <= 1024


def test_replace_photo_deletes_old_file(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers)

    first = client.post(
        f"/api/doctors/{doctor['id']}/photo",
        files={"file": ("first.jpg", _jpeg_bytes(color=(255, 0, 0)), "image/jpeg")},
        headers=admin_headers,
    ).json()
    first_path = _photo_path_on_disk(first["photo_url"])
    assert first_path.is_file()

    second = client.post(
        f"/api/doctors/{doctor['id']}/photo",
        files={"file": ("second.jpg", _jpeg_bytes(color=(0, 0, 255)), "image/jpeg")},
        headers=admin_headers,
    ).json()

    assert second["photo_url"] != first["photo_url"]
    assert not first_path.exists()
    assert _photo_path_on_disk(second["photo_url"]).is_file()


def test_remove_photo(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers)

    uploaded = client.post(
        f"/api/doctors/{doctor['id']}/photo",
        files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")},
        headers=admin_headers,
    ).json()
    photo_path = _photo_path_on_disk(uploaded["photo_url"])
    assert photo_path.is_file()

    response = client.delete(f"/api/doctors/{doctor['id']}/photo", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["photo_url"] is None
    assert not photo_path.exists()

    profile = client.get(f"/api/doctors/{doctor['id']}").json()
    assert profile["photo_url"] is None


def test_remove_photo_when_none_set_is_a_no_op(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers)

    response = client.delete(f"/api/doctors/{doctor['id']}/photo", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["photo_url"] is None


def test_upload_photo_requires_admin(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers)
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    response = client.post(
        f"/api/doctors/{doctor['id']}/photo",
        files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")},
        headers=staff_headers,
    )

    assert response.status_code == 403


def test_upload_photo_for_nonexistent_doctor_returns_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    response = client.post(
        "/api/doctors/999999999/photo",
        files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")},
        headers=admin_headers,
    )

    assert response.status_code == 404


def test_photo_is_served_from_media_mount(client, db_connection):
    """Confirms the uploaded file is actually reachable at the URL the
    API returned -- not just present on disk -- via the same TestClient
    used for every other API call (app.mount("/media", ...) in
    app/main.py is part of the same ASGI app, so this is a real,
    unmocked round trip through StaticFiles)."""
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers)

    uploaded = client.post(
        f"/api/doctors/{doctor['id']}/photo",
        files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")},
        headers=admin_headers,
    ).json()

    served = client.get(uploaded["photo_url"])

    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/")
