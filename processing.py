"""Image model and non-destructive processing pipeline for Luma Studio."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


FILTERS = ("Оригинал", "Ч/Б", "Сепия", "Яркий", "Мягкий", "Холодный", "Тёплый", "Резкость")
EXPORT_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}


@dataclass(frozen=True)
class EditState:
    rotation: int = 0
    size: Optional[tuple[int, int]] = None
    filter_name: str = "Оригинал"
    brightness: int = 100
    contrast: int = 100
    saturation: int = 100


class ImageDocument:
    """One image with independent edits, undo, and redo history."""

    def __init__(self, image: Image.Image, name: str, path: Optional[Path] = None):
        image = ImageOps.exif_transpose(image)
        if image.mode == "P" and "transparency" in image.info:
            image = image.convert("RGBA")
        self.original = image.copy()
        self.name = name
        self.path = path
        self.state = EditState()
        self.undo_stack: list[EditState] = []
        self.redo_stack: list[EditState] = []

    @classmethod
    def open(cls, path: Path) -> "ImageDocument":
        with Image.open(path) as image:
            image.load()
            return cls(image, path.name, path)

    @property
    def natural_size(self) -> tuple[int, int]:
        w, h = self.original.size
        return (h, w) if self.state.rotation % 180 else (w, h)

    @property
    def output_size(self) -> tuple[int, int]:
        return self.state.size or self.natural_size

    @property
    def changed(self) -> bool:
        return self.state != EditState()

    def apply(self, **changes: object) -> None:
        updated = replace(self.state, **changes)
        if updated != self.state:
            self.undo_stack.append(self.state)
            self.redo_stack.clear()
            self.state = updated

    def rotate(self, degrees: int) -> None:
        # Keep the visible aspect ratio after a quarter turn, including resized images.
        size = self.state.size
        if size and degrees % 180:
            size = (size[1], size[0])
        self.apply(rotation=(self.state.rotation + degrees) % 360, size=size)

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append(self.state)
        self.state = self.undo_stack.pop()
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append(self.state)
        self.state = self.redo_stack.pop()
        return True

    def reset(self) -> None:
        self.apply(**vars(EditState()))

    def render(self, preview_limit: Optional[tuple[int, int]] = None) -> Image.Image:
        """Render full resolution for export or a fast downsampled preview."""
        state = self.state
        image = self.original.copy()
        if state.rotation:
            image = image.rotate(-state.rotation, expand=True, resample=Image.Resampling.BICUBIC)

        target = state.size or image.size
        if preview_limit:
            ratio = min(1.0, preview_limit[0] / target[0], preview_limit[1] / target[1])
            target = (max(1, round(target[0] * ratio)), max(1, round(target[1] * ratio)))
        if image.size != target:
            image = image.resize(target, Image.Resampling.LANCZOS)

        alpha = image.getchannel("A") if "A" in image.getbands() else None
        image = image.convert("RGB")
        if state.filter_name == "Ч/Б":
            image = ImageOps.grayscale(image).convert("RGB")
        elif state.filter_name == "Сепия":
            image = ImageOps.colorize(ImageOps.grayscale(image), "#30252a", "#f7dfb6")
        elif state.filter_name == "Яркий":
            image = ImageEnhance.Color(image).enhance(1.45)
            image = ImageEnhance.Contrast(image).enhance(1.10)
        elif state.filter_name == "Мягкий":
            image = image.filter(ImageFilter.GaussianBlur(radius=1.6))
            image = ImageEnhance.Contrast(image).enhance(0.94)
        elif state.filter_name == "Холодный":
            image = Image.blend(image, Image.new("RGB", image.size, "#5279a8"), 0.15)
        elif state.filter_name == "Тёплый":
            image = Image.blend(image, Image.new("RGB", image.size, "#edab67"), 0.16)
        elif state.filter_name == "Резкость":
            image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=165, threshold=3))

        image = ImageEnhance.Brightness(image).enhance(state.brightness / 100)
        image = ImageEnhance.Contrast(image).enhance(state.contrast / 100)
        image = ImageEnhance.Color(image).enhance(state.saturation / 100)
        if alpha:
            image.putalpha(alpha)
        return image

    def save(self, path: Path) -> None:
        fmt = EXPORT_FORMATS.get(path.suffix.lower())
        if not fmt:
            raise ValueError("Неподдерживаемый формат файла")
        image = self.render()
        if fmt in {"JPEG", "BMP"} and image.mode == "RGBA":
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        options = {"quality": 94, "optimize": True} if fmt in {"JPEG", "WEBP"} else {}
        image.save(path, format=fmt, **options)
