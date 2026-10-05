"""Service-specific input validation stays outside the shared job queue."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UploadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["upload"]


class ThumbnailOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: float = Field(default=0, ge=0, le=3600, allow_inf_nan=False)


class ImageInput(UploadInput):
    extension: Literal[
        "png",
        "jpg",
        "jpeg",
        "jfif",
        "gif",
        "webp",
        "bmp",
        "ico",
        "tif",
        "tiff",
        "heic",
        "heif",
        "avif",
    ]


class ImageOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Service:
    kind: str
    service: str
    label: str
    input_type: str
    options: type[BaseModel]
    result_filename: str | None = None
    result_media_type: str | None = None
    input_model: type[BaseModel] = UploadInput


SERVICES = {
    "image.package": Service(
        "image.package",
        "image",
        "이미지 통합 처리 · 썸네일 + WebP 미리보기",
        "upload",
        ImageOptions,
        input_model=ImageInput,
    ),
    "video.package": Service(
        "video.package",
        "ffmpeg",
        "영상 통합 처리 · 썸네일 + 해상도별 스트리밍",
        "upload",
        ThumbnailOptions,
    ),
    "video.thumbnail": Service(
        "video.thumbnail",
        "ffmpeg",
        "영상 썸네일",
        "upload",
        ThumbnailOptions,
        "thumbnail.jpg",
        "image/jpeg",
    ),
}
