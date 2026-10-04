import base64
import binascii
import io
import math
from urllib.parse import urlparse, parse_qs

from flask import Flask, request, jsonify, render_template
from PIL import Image, UnidentifiedImageError
from merchant_store import MIN_SHOP_RADIUS_M, get_merchant_by_vpa, initialize_database, list_merchants
from tamper_detection import analyze_image, decode_image


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
initialize_database()


def load_merchants():
    """Compatibility helper returning the SQLite directory keyed by VPA."""
    return {row["vpa"].lower(): row for row in list_merchants()}


def parse_upi_payload(raw_string):
    """
    Parse a UPI payload string like:
    upi://pay?pa=merchant@okaxis&pn=Merchant%20Name&mc=5411&tr=TXN123&am=250.00&cu=INR
    Returns a dict of extracted fields, or None if it isn't a valid upi:// payload.
    """
    if not raw_string or not raw_string.lower().startswith("upi://"):
        return None
    parsed = urlparse(raw_string)
    qs = parse_qs(parsed.query)
    fields = {k: v[0] for k, v in qs.items()}
    return {
        "pa": fields.get("pa", "").strip(),
        "pn": fields.get("pn", "").strip(),
        "mc": fields.get("mc", "").strip(),
        "tr": fields.get("tr", "").strip(),
        "am": fields.get("am", "").strip(),
        "cu": fields.get("cu", "").strip(),
    }


def check_identity(pa, pn):
    """
    Core decision engine.
    Returns one of: 'trusted', 'suspicious', 'unverifiable'
    plus a human-readable reason.
    """
    record = get_merchant_by_vpa((pa or "").strip())
    if record is None:
        return "unverifiable", "VPA not found in trusted merchant database."
    db_name = record["merchant_name"].strip().lower()
    qr_name = (pn or "").strip().lower()

    if db_name == qr_name:
        return "trusted", f"VPA and merchant name match registered record ({record['merchant_name']})."
    else:
        return (
            "suspicious",
            f"VPA is registered to '{record['merchant_name']}' but QR shows '{pn}'. Possible tampering.",
        )


def haversine_meters(latitude_a, longitude_a, latitude_b, longitude_b):
    """Calculate great-circle distance between two GPS coordinates."""
    earth_radius_m = 6371000
    lat_a, lat_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_lat = math.radians(latitude_b - latitude_a)
    delta_lon = math.radians(longitude_b - longitude_a)
    haversine = (math.sin(delta_lat / 2) ** 2
                 + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2)
    return 2 * earth_radius_m * math.asin(math.sqrt(haversine))


def image_from_request(data):
    """Read a multipart image or base64 data URL captured by the browser."""
    uploaded = request.files.get("frame") or request.files.get("qr_image")
    if uploaded:
        return uploaded.read()
    encoded = data.get("frame", "")
    if encoded.startswith("data:") and "," in encoded:
        encoded = encoded.split(",", 1)[1]
    if encoded:
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None
    return None


def verify_request(data):
    """Apply identity, geofence, and image-heuristic checks to one scan."""
    raw = (data.get("raw") or data.get("payload") or "").strip()
    image_bytes = image_from_request(data)
    if not raw and image_bytes:
        raw = decode_image(image_bytes) or ""
    fields = parse_upi_payload(raw)
    if fields is None:
        return {"status": "error", "message": "Not a valid UPI QR payload."}, 400

    verdict, identity_reason = check_identity(fields["pa"], fields["pn"])
    merchant = get_merchant_by_vpa(fields["pa"])
    gps = data.get("gps") or {}
    latitude = gps.get("latitude", data.get("latitude"))
    longitude = gps.get("longitude", data.get("longitude"))
    accuracy_m = gps.get("accuracy", data.get("accuracy_m"))
    distance_m = None
    location_flag = False
    location_reason = "Device location unavailable; location check skipped"
    radius_m = float(merchant["radius_m"]) if merchant else MIN_SHOP_RADIUS_M

    try:
        latitude = float(latitude) if latitude is not None else None
        longitude = float(longitude) if longitude is not None else None
        accuracy_m = float(accuracy_m) if accuracy_m is not None else 0.0
        if (latitude is None or longitude is None
                or not -90 <= latitude <= 90 or not -180 <= longitude <= 180
                or not math.isfinite(accuracy_m) or accuracy_m < 0):
            latitude = longitude = None
            accuracy_m = 0.0
    except (TypeError, ValueError):
        latitude = longitude = None
        accuracy_m = 0.0

    if merchant and latitude is not None and longitude is not None:
        distance_m = haversine_meters(
            float(latitude), float(longitude), merchant["latitude"], merchant["longitude"]
        )
        location_flag = distance_m > radius_m
        location_reason = (
            f"You are {distance_m / 1000:.1f} km from this shop" if location_flag
            else f"Within shop radius ({distance_m:.2f} m / {radius_m:.2f} m)"
        )

    tamper = analyze_image(image_bytes) if image_bytes else {
        "score": 0, "threshold": 55, "reasons": ["No captured image supplied"],
        "qr_count": 0, "available": False,
    }
    tamper_flag = tamper["score"] > tamper["threshold"]
    flags = []
    if verdict == "suspicious":
        flags.append(identity_reason)
    elif verdict == "unverifiable":
        flags.append("VPA not found in trusted merchant database")
    if location_flag:
        flags.append(location_reason)
    if tamper_flag:
        flags.extend(tamper["reasons"])
    identity_flag = verdict == "suspicious"
    if verdict != "unverifiable" and (verdict == "suspicious" or location_flag or tamper_flag):
        verdict = "suspicious"
    risk_level = (
        "red" if verdict == "unverifiable" or (location_flag and (identity_flag or tamper_flag))
        else "yellow" if verdict == "suspicious" or location_flag or tamper_flag
        else "green"
    )
    findings = list(flags)
    if merchant and not identity_flag:
        if merchant["registry_verification_status"] == "sample_only":
            findings.append("Demo registry record; bank and address verification unavailable")
        else:
            findings.append("Merchant database match: VPA and name registered")
    if location_reason != "Location not checked":
        findings.append(location_reason)
    findings.extend(tamper["reasons"])
    findings = list(dict.fromkeys(findings))

    return {
        "status": "ok", "verdict": verdict,
        "risk_level": risk_level,
        "reason": "; ".join(flags) if flags else identity_reason,
        "fields": fields,
        "merchant_id": merchant["merchant_id"] if merchant else None,
        "registered_merchant_name": merchant["merchant_name"] if merchant else None,
        "registered_shop_address": merchant["shop_address"] if merchant else None,
        "registry": {
            "status": merchant["registry_verification_status"] if merchant else "not_found",
            "bank_verified": bool(merchant and merchant["bank_verified_merchant_id"]
                                  and merchant["registry_verification_status"] == "verified"),
            "address_verified": bool(merchant and merchant["shop_address"]
                                     and merchant["registry_verification_status"] == "verified"),
        },
        "location": {
            "mode": "device_gps",
            "available": distance_m is not None,
            "distance_m": round(distance_m, 1) if distance_m is not None else None,
            "radius_m": round(radius_m, 2), "accuracy_m": round(accuracy_m, 1),
            "outside_radius": location_flag, "reason": location_reason,
        },
        "tamper": tamper, "flags": flags, "findings": findings, "raw": raw,
    }, 200


@app.route("/")
def index():
    return render_template("index.html")


@app.errorhandler(413)
def request_too_large(_error):
    """Return a readable limit error for oversized camera/upload frames."""
    return jsonify({
        "status": "error",
        "message": "Image is larger than 12 MB. Retake the scan or choose a smaller image.",
    }), 413


@app.route("/check_payload", methods=["POST"])
def check_payload():
    """Used by the live-camera flow: browser decodes QR via jsQR, sends raw string here."""
    data = request.get_json(force=True)
    raw = data.get("raw", "")
    fields = parse_upi_payload(raw)
    if fields is None:
        return jsonify({"status": "error", "message": "Not a valid UPI QR payload."}), 400

    status, reason = check_identity(fields["pa"], fields["pn"])
    return jsonify({"status": "ok", "verdict": status, "reason": reason, "fields": fields})


@app.route("/verify", methods=["POST"])
def verify():
    """Combined endpoint for the live camera and uploaded image flows."""
    data = request.get_json(silent=True) or request.form
    result, status_code = verify_request(data)
    return jsonify(result), status_code


@app.route("/check_image", methods=["POST"])
def check_image():
    """Used by the file-upload flow: server decodes the QR image itself."""
    if "qr_image" not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded."}), 400

    file = request.files["qr_image"]
    try:
        img = Image.open(io.BytesIO(file.read()))
        img.load()
    except (OSError, UnidentifiedImageError):
        return jsonify({"status": "error", "message": "The uploaded file is not a readable image."}), 400

    file.stream.seek(0)
    raw = decode_image(file.read())
    if not raw:
        return jsonify({"status": "error", "message": "No readable QR code detected in image."}), 400
    fields = parse_upi_payload(raw)
    if fields is None:
        return jsonify({"status": "error", "message": "QR does not contain a valid UPI payload."}), 400

    status, reason = check_identity(fields["pa"], fields["pn"])
    merchant_record = load_merchants().get(fields["pa"].strip().lower())
    return jsonify({
        "status": "ok",
        "verdict": status,
        "reason": reason,
        "fields": fields,
        "merchant_id": merchant_record["merchant_id"] if merchant_record else None,
        "registered_merchant_name": merchant_record["merchant_name"] if merchant_record else None,
        "raw": raw,
    })


if __name__ == "__main__":
    import os

    certificate = os.environ.get("SHIELDPAY_TLS_CERT")
    private_key = os.environ.get("SHIELDPAY_TLS_KEY")
    tls_context = (certificate, private_key) if certificate and private_key else None
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")),
            debug=False, ssl_context=tls_context)