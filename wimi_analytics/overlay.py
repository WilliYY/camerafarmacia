import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageDraw, ImageFont


ROLE_LABELS = {
    "authorized": "Autorizado",
    "contractor": "Prestador",
    "employee": "Funcionario",
    "manager": "Gerente",
}
KNOWN_COLOR = (16, 185, 129)
UNKNOWN_COLOR = (245, 158, 11)
LABEL_BACKGROUND = (8, 12, 18)


def _clean_label(value, fallback, limit=40):
    cleaned = " ".join(str(value or "").split())[:limit]
    return cleaned or fallback


@lru_cache(maxsize=4)
def _load_font(size=14):
    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for filename in ("segoeuib.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(str(windows_dir / "Fonts" / filename), size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def _face_label(face):
    if face.get("provisional"):
        name = _clean_label(face.get("display_name"), "Pessoa em analise")
        confidence = face.get("confidence")
        parts = [name, "Em analise"]
        if isinstance(confidence, (int, float)):
            parts.append(f"{max(0, min(round(confidence * 100), 100))}%")
        return " | ".join(parts)
    if not face.get("recognized"):
        return "Desconhecido"
    name = _clean_label(face.get("display_name"), "Pessoa cadastrada")
    role = ROLE_LABELS.get(str(face.get("role") or "").lower())
    confidence = face.get("confidence")
    parts = [name]
    if role:
        parts.append(role)
    if isinstance(confidence, (int, float)):
        parts.append(f"{max(0, min(round(confidence * 100), 100))}%")
    return " | ".join(parts)


def _fit_label(draw, label, font, max_width):
    label = _clean_label(label, "Pessoa", limit=80)
    while len(label) > 4:
        bounds = draw.textbbox((0, 0), label, font=font)
        if bounds[2] - bounds[0] <= max_width:
            return label, bounds
        label = label[:-4].rstrip() + "..."
    bounds = draw.textbbox((0, 0), label, font=font)
    return label, bounds


def render_identity_overlay(image, overlay):
    faces = list((overlay or {}).get("faces") or [])
    if not faces:
        return image

    output = image.convert("RGB")
    draw = ImageDraw.Draw(output)
    font = _load_font()
    source_width, source_height = (overlay or {}).get("source_size") or image.size
    source_width = max(1, int(source_width))
    source_height = max(1, int(source_height))
    scale_x = output.width / source_width
    scale_y = output.height / source_height
    line_width = 3

    for face in faces[:16]:
        raw_bbox = face.get("bbox")
        if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
            continue
        try:
            x, y, width, height = (float(value) for value in raw_bbox)
        except (TypeError, ValueError):
            continue
        x1 = max(0, min(round(x * scale_x), max(0, output.width - 2)))
        y1 = max(0, min(round(y * scale_y), max(0, output.height - 2)))
        x2 = max(x1 + 1, min(round((x + width) * scale_x), output.width - 1))
        y2 = max(y1 + 1, min(round((y + height) * scale_y), output.height - 1))
        recognized = bool(face.get("recognized"))
        color = KNOWN_COLOR if recognized else UNKNOWN_COLOR
        draw.rectangle((x1, y1, x2, y2), outline=color, width=line_width)

        label, bounds = _fit_label(
            draw,
            _face_label(face),
            font,
            max(40, output.width - 12),
        )
        text_width = bounds[2] - bounds[0]
        text_height = bounds[3] - bounds[1]
        label_x = min(x1, max(4, output.width - text_width - 12))
        label_y = y1 - text_height - 10
        if label_y < 4:
            label_y = min(output.height - text_height - 8, y1 + 6)
        draw.rectangle(
            (
                label_x,
                label_y,
                min(output.width - 1, label_x + text_width + 10),
                min(output.height - 1, label_y + text_height + 6),
            ),
            fill=LABEL_BACKGROUND,
            outline=color,
            width=1,
        )
        draw.text((label_x + 5, label_y + 2), label, fill=color, font=font)

    return output
