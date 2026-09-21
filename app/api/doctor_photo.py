import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.api.staff_auth import require_permission
from app.config import MEDIA_ROOT
from app.db.connection import get_connection
from app.services.audit_log import record_audit_log

router = APIRouter(prefix="/doctors", tags=["Doctors"])

# Kept deliberately generous but bounded: this is a professional profile
# photo, not a general-purpose file upload.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2MB
# Guards against decompression-bomb-style uploads (a small file that
# decodes to an enormous pixel buffer) -- checked on the *decoded* image
# size, not just the compressed file size above.
MAX_SOURCE_DIMENSION = 6000
# Anything larger than this on its longest edge is downscaled rather than
# rejected -- a professional headshot never needs to be stored bigger
# than this for display on a doctor card or profile view.
STORED_MAX_EDGE = 1024

ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}

DOCTOR_PHOTOS_DIR = MEDIA_ROOT / "doctors"


def _doctor_photo_url(filename: str) -> str:
    return f"/media/doctors/{filename}"


def _delete_photo_file(photo_url: str) -> None:
    # Safe by construction, not by sanitizing client input: the filename
    # is always one this endpoint generated itself (a uuid4 hex + a fixed
    # extension from ALLOWED_FORMATS), and photo_url is only ever read
    # back from our own doctors.photo_url column -- never taken directly
    # from a request. Path(...).name still strips any directory
    # component defensively before joining.
    path = DOCTOR_PHOTOS_DIR / Path(photo_url).name
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # Best-effort cleanup -- a stray orphaned file is not worth
        # failing the request that already succeeded at its actual job
        # (uploading the new photo / clearing the reference).
        pass


@router.post("/{doctor_id}/photo")
async def upload_doctor_photo(
    doctor_id: int,
    file: UploadFile = File(...),
    admin: dict = Depends(require_permission("doctor.manage")),
):
    raw = await file.read()

    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Photo must be 2MB or smaller")

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=400, detail="File is not a valid image")

    image_format = image.format
    if image_format not in ALLOWED_FORMATS:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, or WebP photos are supported")

    if max(image.size) > MAX_SOURCE_DIMENSION:
        raise HTTPException(status_code=400, detail="Image dimensions are too large")

    # JPEG has no alpha channel -- collapse RGBA/palette-with-transparency
    # down to RGB before saving as JPEG, or Pillow raises on save.
    if image_format == "JPEG" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    if max(image.size) > STORED_MAX_EDGE:
        image.thumbnail((STORED_MAX_EDGE, STORED_MAX_EDGE), Image.LANCZOS)

    extension = ALLOWED_FORMATS[image_format]
    filename = f"{uuid.uuid4().hex}.{extension}"

    DOCTOR_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    destination = DOCTOR_PHOTOS_DIR / filename
    # Re-encoding through Pillow (rather than writing `raw` verbatim)
    # strips EXIF/metadata and applies the downscale above -- the saved
    # file is never simply a byte-for-byte copy of whatever was uploaded.
    image.save(destination, format=image_format)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT photo_url FROM doctors WHERE id = %s",
                (doctor_id,),
            )
            row = cur.fetchone()

            if row is None:
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=404, detail="Doctor not found")

            previous_photo_url = row[0]
            new_photo_url = _doctor_photo_url(filename)

            cur.execute(
                "UPDATE doctors SET photo_url = %s, updated_at = NOW() WHERE id = %s",
                (new_photo_url, doctor_id),
            )

            record_audit_log(
                cur,
                hospital_id=admin["hospital_id"],
                staff_id=admin["id"],
                action="doctor.photo_upload",
                resource_type="doctor",
                resource_id=doctor_id,
            )

    # Only remove the previous file once the new one is safely referenced
    # by the DB row -- if anything above failed, the old photo stays put.
    if previous_photo_url:
        _delete_photo_file(previous_photo_url)

    return {"doctor_id": doctor_id, "photo_url": new_photo_url}


@router.delete("/{doctor_id}/photo")
def remove_doctor_photo(
    doctor_id: int,
    admin: dict = Depends(require_permission("doctor.manage")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT photo_url FROM doctors WHERE id = %s",
                (doctor_id,),
            )
            row = cur.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="Doctor not found")

            previous_photo_url = row[0]

            cur.execute(
                "UPDATE doctors SET photo_url = NULL, updated_at = NOW() WHERE id = %s",
                (doctor_id,),
            )

            record_audit_log(
                cur,
                hospital_id=admin["hospital_id"],
                staff_id=admin["id"],
                action="doctor.photo_remove",
                resource_type="doctor",
                resource_id=doctor_id,
            )

    if previous_photo_url:
        _delete_photo_file(previous_photo_url)

    return {"doctor_id": doctor_id, "photo_url": None}
