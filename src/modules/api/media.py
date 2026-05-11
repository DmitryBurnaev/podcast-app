import os
from hashlib import md5
from typing import Annotated

from litestar import post
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.params import Body

from src.modules.api.base import BaseApiController
from src.modules.api.errors import InvalidParametersError
from src.modules.services.storage import StorageS3
from src.modules.utils import ffmpeg as ffmpeg_utils
from src.modules.utils.processing import get_file_size, save_uploaded_file
from src.schemas import UploadedAudioData, UploadedImageData
from src.settings.app import get_app_settings


class MediaUploadAPIController(BaseApiController):
    path = "/api/media/upload"
    tags = ["Media"]

    @post("/audio/")
    async def upload_audio(
        self,
        data: Annotated[dict[str, UploadFile], Body(media_type=RequestEncodingType.MULTI_PART)],
    ) -> UploadedAudioData:
        uploaded_file = _get_upload(data)
        if not uploaded_file.content_type.startswith("audio/"):
            raise InvalidParametersError(details={"file": "File must be audio."})

        settings = get_app_settings()
        local_path = await save_uploaded_file(
            uploaded_file,
            prefix="uploaded_",
            max_file_size=settings.max_upload_audio_filesize,
            tmp_path=settings.tmp_audio_path,
        )
        metadata = ffmpeg_utils.audio_metadata(local_path)
        metadata_dict = metadata._asdict()
        uploaded_hash = _hash_upload(
            uploaded_file.filename, get_file_size(local_path), metadata_dict
        )
        remote_name = f"uploaded_{uploaded_hash}{os.path.splitext(uploaded_file.filename)[-1]}"
        remote_path = await StorageS3().upload_file(
            local_path,
            dst_path=settings.s3.bucket_tmp_audio_path,
            filename=remote_name,
        )
        if not remote_path:
            raise InvalidParametersError(details={"file": "Could not upload audio file."})

        cover_data = await _upload_audio_cover(local_path)
        return UploadedAudioData(
            name=uploaded_file.filename,
            path=remote_path,
            size=get_file_size(local_path),
            meta=metadata_dict,
            hash=uploaded_hash,
            cover=cover_data,
        )

    @post("/image/")
    async def upload_image(
        self,
        data: Annotated[dict[str, UploadFile], Body(media_type=RequestEncodingType.MULTI_PART)],
    ) -> UploadedImageData:
        uploaded_file = _get_upload(data)
        if not uploaded_file.content_type.startswith("image/"):
            raise InvalidParametersError(details={"file": "File must be image."})

        settings = get_app_settings()
        local_path = await save_uploaded_file(
            uploaded_file,
            prefix="uploaded_image_",
            max_file_size=settings.max_upload_image_filesize,
            tmp_path=settings.tmp_image_path,
        )
        uploaded_hash = _hash_upload(uploaded_file.filename, get_file_size(local_path), None)
        remote_name = f"uploaded_{uploaded_hash}{os.path.splitext(uploaded_file.filename)[-1]}"
        remote_path = await StorageS3().upload_file(
            local_path,
            dst_path=settings.s3.bucket_tmp_images_path,
            filename=remote_name,
        )
        if not remote_path:
            raise InvalidParametersError(details={"file": "Could not upload image file."})

        preview_url = await StorageS3().get_presigned_url(remote_path)
        return UploadedImageData(
            name=uploaded_file.filename,
            path=remote_path,
            size=get_file_size(local_path),
            hash=uploaded_hash,
            preview_url=preview_url,
        )


def _get_upload(data: dict[str, UploadFile]) -> UploadFile:
    uploaded_file = data.get("file") or next(iter(data.values()), None)
    if not isinstance(uploaded_file, UploadFile):
        raise InvalidParametersError(details={"file": "File is required."})
    return uploaded_file


def _hash_upload(filename: str, filesize: int, metadata: dict | None) -> str:
    data = {"filename": filename, "filesize": filesize}
    if metadata:
        data |= metadata
    return md5(str(data).encode()).hexdigest()


async def _upload_audio_cover(audio_path) -> UploadedImageData | None:
    cover = ffmpeg_utils.audio_cover(audio_path)
    if cover is None:
        return None

    storage = StorageS3()
    remote_path = await storage.upload_file(
        cover.path,
        dst_path=get_app_settings().s3.bucket_images_path,
        filename=cover.path.name,
    )
    if not remote_path:
        return None

    return UploadedImageData(
        name=cover.path.name,
        path=remote_path,
        hash=cover.hash,
        size=cover.size,
        preview_url=await storage.get_presigned_url(remote_path),
    )
