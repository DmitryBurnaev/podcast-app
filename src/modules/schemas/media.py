from pydantic import BaseModel


class UploadedImageData(BaseModel):
    """Metadata for an uploaded image."""

    name: str | None = None
    path: str
    hash: str
    size: int
    preview_url: str | None = None


class UploadedAudioData(BaseModel):
    """Metadata for an uploaded audio file."""

    name: str
    path: str
    size: int
    meta: dict
    hash: str
    cover: UploadedImageData | None = None
