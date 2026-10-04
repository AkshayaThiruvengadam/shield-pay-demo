"""Explainable, image-only QR tamper heuristics (not a forensic classifier)."""
import numpy as np

try:
    import cv2
except ImportError:  # The API remains usable for identity checks without OpenCV.
    cv2 = None


def _decode_qrs(image):
    detector = cv2.QRCodeDetector()
    try:
        found, decoded, points, _ = detector.detectAndDecodeMulti(image)
        if found and points is not None:
            values = [value for value in decoded if value]
            return values, np.asarray(points, dtype=np.float32)
    except (cv2.error, ValueError):
        pass
    text, points, _ = detector.detectAndDecode(image)
    if points is None:
        return [], None
    return ([text] if text else []), np.asarray(points, dtype=np.float32).reshape(1, -1, 2)


def decode_image(image_bytes):
    """Decode the first QR payload using OpenCV, then pyzbar if available."""
    if cv2 is not None:
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            values, _ = _decode_qrs(image)
            if values:
                return values[0]
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode as zbar_decode
        import io

        decoded = zbar_decode(Image.open(io.BytesIO(image_bytes)))
        if decoded:
            return decoded[0].data.decode("utf-8")
    except (ImportError, OSError, UnicodeDecodeError):
        pass
    return None


def analyze_image(image_bytes):
    """Return a conservative score and human-readable heuristic signals."""
    result = {"score": 0, "threshold": 55, "reasons": [], "qr_count": 0,
              "available": cv2 is not None}
    if cv2 is None:
        result["reasons"].append("OpenCV heuristics unavailable")
        return result

    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        result["reasons"].append("Image could not be analyzed")
        return result

    values, points = _decode_qrs(image)
    detected_qr_count = len(points) if points is not None else len(values)
    result["qr_count"] = detected_qr_count
    score = 0
    reasons = []
    if detected_qr_count > 1:
        score += 75
        reasons.append("Second QR detected")

    if points is not None and len(points):
        quad = points[0].reshape(-1, 2)
        x0, y0 = np.floor(quad.min(axis=0)).astype(int)
        x1, y1 = np.ceil(quad.max(axis=0)).astype(int)
        height, width = image.shape[:2]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(width, x1), min(height, y1)
        qr = image[y0:y1, x0:x1]
        if qr.size and x1 > x0 and y1 > y0:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            qr_width, qr_height = x1 - x0, y1 - y0
            margin = max(2, int(min(qr_width, qr_height) * 0.06))
            band = gray[max(0, y0 - margin):min(height, y1 + margin),
                        max(0, x0 - margin):min(width, x1 + margin)]
            if band.size:
                border_pixels = np.concatenate((band[0, :], band[-1, :], band[:, 0], band[:, -1]))
                dark_ratio = float(np.mean(border_pixels < 200))
                result["quiet_zone_dark_ratio"] = round(dark_ratio, 3)
                if dark_ratio > 0.18:
                    score += 20
                    reasons.append("Quiet-zone/border irregularity")

            # Compare the QR crop with a narrow surrounding ring. Large changes can
            # indicate a pasted patch, but lighting and camera focus also affect it.
            outer = gray[max(0, y0 - margin * 3):min(height, y1 + margin * 3),
                         max(0, x0 - margin * 3):min(width, x1 + margin * 3)]
            if outer.size and qr_width > 4 and qr_height > 4:
                qr_gray = gray[y0:y1, x0:x1]
                inside_sharpness = float(cv2.Laplacian(qr_gray, cv2.CV_64F).var())
                outside_sharpness = float(cv2.Laplacian(outer, cv2.CV_64F).var())
                brightness_gap = abs(float(qr_gray.mean()) - float(outer.mean()))
                sharpness_ratio = max(inside_sharpness, outside_sharpness, 1.0) / max(
                    min(inside_sharpness, outside_sharpness), 1.0
                )
                result["brightness_gap"] = round(brightness_gap, 1)
                result["sharpness_ratio"] = round(sharpness_ratio, 2)
                if brightness_gap > 75 or sharpness_ratio > 12:
                    score += 15
                    reasons.append("QR/surrounding sharpness or brightness mismatch")

                # A pasted QR can have a much finer module pattern than the
                # underlying print. Compare small tiles to find localized texture.
                tile_edges = cv2.Canny(qr_gray, 70, 160)
                tile_height, tile_width = tile_edges.shape
                edge_density = []
                for tile_y in range(8):
                    for tile_x in range(8):
                        tile = tile_edges[
                            tile_y * tile_height // 8:(tile_y + 1) * tile_height // 8,
                            tile_x * tile_width // 8:(tile_x + 1) * tile_width // 8,
                        ]
                        if tile.size:
                            edge_density.append(float(np.mean(tile > 0)))
                edge_density.sort()
                if len(edge_density) >= 8:
                    baseline = float(np.median(edge_density))
                    fourth_highest = edge_density[-4]
                    result["local_edge_density_ratio"] = round(
                        fourth_highest / max(baseline, 0.01), 2
                    )
                    if fourth_highest > 0.13 and fourth_highest > baseline * 1.7:
                        score += 60
                        reasons.append("Possible sticker overlay: localized QR texture mismatch")

            # A strong independent rectangular edge crossing the QR box may be an
            # applied label. This is intentionally a low-confidence signal.
            crop = gray[max(0, y0 - qr_height):min(height, y1 + qr_height),
                        max(0, x0 - qr_width):min(width, x1 + qr_width)]
            if crop.size:
                edges = cv2.Canny(crop, 70, 160)
                lines = cv2.HoughLinesP(
                    edges, 1, np.pi / 180, threshold=max(18, min(qr_width, qr_height) // 5),
                    minLineLength=max(12, int(min(qr_width, qr_height) * 0.42)),
                    maxLineGap=8,
                )
                if lines is not None:
                    angles = []
                    for line in np.asarray(lines).reshape(-1, 4):
                        dx, dy = int(line[2] - line[0]), int(line[3] - line[1])
                        angle = abs(float(np.degrees(np.arctan2(dy, dx)))) % 90
                        if min(angle, 90 - angle) > 12:
                            angles.append(angle)
                    if len(angles) >= 4:
                        score += 15
                        reasons.append("Possible sticker edge or rotated overlay")

    result["score"] = min(score, 100)
    result["reasons"] = reasons or ["No strong image-tamper heuristic triggered"]
    return result