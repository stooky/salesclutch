import os
import uuid
from pathlib import Path
from fastapi import UploadFile

UPLOAD_DIR = Path("uploads")


async def save_upload(file: UploadFile) -> tuple[str, str]:
    """
    Save uploaded file and return (saved_path, original_filename).
    """
    UPLOAD_DIR.mkdir(exist_ok=True)

    # Generate unique filename to avoid collisions
    ext = Path(file.filename).suffix
    unique_name = f"{uuid.uuid4()}{ext}"
    save_path = UPLOAD_DIR / unique_name

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    return str(save_path), file.filename


def cleanup_upload(file_path: str):
    """Remove uploaded file after processing."""
    try:
        os.remove(file_path)
    except OSError:
        pass
