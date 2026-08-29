FRAME_SAMPLE_SIZE = (48, 27)
FRAME_RECONNECT_AFTER = 8


def _pixel_distance(left, right):
    return sum(abs(int(a) - int(b)) for a, b in zip(left, right)) / 3.0


def _band_metrics(pixels, width, start_y, end_y):
    band = [pixels[y * width + x] for y in range(start_y, end_y) for x in range(width)]
    count = max(1, len(band))
    washed = sum(
        1
        for red, green, blue in band
        if (red + blue) / 2 >= 210 and green >= 165
    ) / count
    pink = sum(
        1
        for red, green, blue in band
        if red >= 205 and blue >= 185 and max(red, blue) - green >= 20
    ) / count

    detail_total = 0.0
    detail_count = 0
    for y in range(start_y, end_y):
        row_start = y * width
        for x in range(width - 1):
            detail_total += _pixel_distance(
                pixels[row_start + x], pixels[row_start + x + 1]
            )
            detail_count += 1
    for y in range(start_y, end_y - 1):
        row_start = y * width
        next_row_start = (y + 1) * width
        for x in range(width):
            detail_total += _pixel_distance(
                pixels[row_start + x], pixels[next_row_start + x]
            )
            detail_count += 1
    detail = detail_total / max(1, detail_count)
    return {"washed": washed, "pink": pink, "detail": detail}


def assess_frame_integrity(image):
    """Classifica artefatos severos de decoder com custo constante e limitado."""
    try:
        width, height = image.size
        if width < 16 or height < 16:
            return {"valid": False, "reason": "invalid_dimensions"}

        rgb_image = image if getattr(image, "mode", None) == "RGB" else image.convert("RGB")
        sampled = rgb_image.resize(FRAME_SAMPLE_SIZE, resample=0)
        sample_width, sample_height = sampled.size
        get_flattened_data = getattr(sampled, "get_flattened_data", None)
        pixels = list(
            get_flattened_data() if callable(get_flattened_data) else sampled.getdata()
        )
        pixel_count = max(1, len(pixels))

        sentinel_count = 0
        for red, green, blue in pixels:
            if red > 200 and green < 70 and blue > 200:
                sentinel_count += 1
            elif red < 70 and green > 200 and blue < 70:
                sentinel_count += 1
            elif 120 <= red <= 136 and 120 <= green <= 136 and 120 <= blue <= 136:
                sentinel_count += 1
        sentinel_ratio = sentinel_count / pixel_count
        if sentinel_ratio >= 0.45:
            return {
                "valid": False,
                "reason": "decoder_sentinel_color",
                "sentinel_ratio": round(sentinel_ratio, 4),
            }

        band_height = sample_height // 3
        bands = [
            _band_metrics(pixels, sample_width, index * band_height, (index + 1) * band_height)
            for index in range(3)
        ]
        dominant = max(bands, key=lambda item: item["washed"])
        washed_spread = dominant["washed"] - min(item["washed"] for item in bands)
        if (
            dominant["washed"] >= 0.82
            and washed_spread >= 0.30
            and dominant["pink"] >= 0.12
            and dominant["detail"] <= 8.0
        ):
            return {
                "valid": False,
                "reason": "horizontal_decode_bands",
                "washed_ratio": round(dominant["washed"], 4),
                "washed_spread": round(washed_spread, 4),
                "pink_ratio": round(dominant["pink"], 4),
                "detail": round(dominant["detail"], 4),
            }

        return {"valid": True, "reason": "ok"}
    except Exception:
        return {"valid": False, "reason": "integrity_check_error"}


class FrameIntegrityGuard:
    def __init__(self, reconnect_after=FRAME_RECONNECT_AFTER):
        self.reconnect_after = max(1, int(reconnect_after))
        self.consecutive_rejected = 0
        self.total_rejected = 0
        self.last_reason = "ok"

    def observe(self, valid, reason="ok"):
        if valid:
            self.consecutive_rejected = 0
            self.last_reason = "ok"
            return False
        self.consecutive_rejected += 1
        self.total_rejected += 1
        self.last_reason = str(reason or "invalid_frame")
        return self.consecutive_rejected >= self.reconnect_after

    def reset(self):
        self.consecutive_rejected = 0
        self.last_reason = "ok"
