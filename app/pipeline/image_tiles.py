"""Read long product screenshots without shrinking their text to a few pixels."""
import io
import math

from PIL import Image

MAX_PIXELS = 25_000_000
TILE_SIDE = 1600
MAX_SHORT_SIDE = 1200
OVERLAP = 120
MAX_TILES = 24


def split_image(data):
    # Small inputs retain the existing OCR path (and original source identity).
    try:
        image = Image.open(io.BytesIO(data))
    except (OSError, ValueError):
        return [data]
    with image:
        width, height = image.size
        if width * height > MAX_PIXELS:
            raise ValueError('이미지 해상도가 너무 큽니다. 제품 설명을 나누어 올려 주세요.')
        if max(width, height) <= TILE_SIDE or max(width, height) / min(width, height) < 2:
            return [data]
        scale = min(1, MAX_SHORT_SIDE / min(width, height))
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        length = max(size)
        count = 1 + math.ceil(max(0, length - TILE_SIDE) / (TILE_SIDE - OVERLAP))
        if count > MAX_TILES:
            raise ValueError('상세 이미지가 너무 깁니다. 제품 설명을 나누어 올려 주세요.')
        pixels = image.convert('RGB')
        if pixels.size != size:
            resized = pixels.resize(size, Image.Resampling.LANCZOS)
            pixels.close()
            pixels = resized
        tiles = []
        try:
            for start in range(0, length, TILE_SIDE - OVERLAP):
                end = min(length, start + TILE_SIDE)
                box = (0, start, size[0], end) if size[1] >= size[0] else (start, 0, end, size[1])
                with pixels.crop(box) as tile:
                    out = io.BytesIO()
                    tile.save(out, format='PNG')
                    tiles.append(out.getvalue())
                if end == length:
                    break
        finally:
            pixels.close()
        return tiles


def join_texts(texts):
    """Remove only exact duplicated boundary lines introduced by overlap."""
    result = []
    for text in texts:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        overlap = 0
        for size in range(1, min(12, len(result), len(lines)) + 1):
            if result[-size:] == lines[:size]:
                overlap = size
        result.extend(lines[overlap:])
    return '\n'.join(result)
