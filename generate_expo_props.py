"""Generate 9 cm A-D QR props and a small E sticker for the expo."""
from pathlib import Path
from urllib.parse import urlencode

import qrcode
from qrcode.constants import ERROR_CORRECT_H

from merchant_store import get_merchant_by_vpa, initialize_database


OUTPUT_DIR = Path(__file__).resolve().parent / "demo_props"
DPI = 300


def payload(vpa, name, category="5411", transaction="EXPO"):
    """Build a normal UPI pay URL with encoded values."""
    return "upi://pay?" + urlencode({
        "pa": vpa, "pn": name, "mc": category, "tr": transaction,
        "am": "10.00", "cu": "INR",
    })


def make_qr(text, side_cm):
    code = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_H,
                         box_size=12, border=4)
    code.add_data(text)
    code.make(fit=True)
    image = code.make_image(fill_color="black", back_color="white").convert("RGB")
    target_pixels = round(side_cm / 2.54 * DPI)
    scale = target_pixels / image.width
    module_pixels = max(1, round(12 * scale))
    code = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_H,
                         box_size=module_pixels, border=4)
    code.add_data(text)
    code.make(fit=True)
    return code.make_image(fill_color="black", back_color="white").convert("RGB")


def main():
    initialize_database()
    merchant = get_merchant_by_vpa("rameshstores@okaxis")
    second = get_merchant_by_vpa("priyabakery@okhdfcbank")
    if not merchant or not second:
        raise SystemExit("Expected M001 and M002 merchants in the SQLite registry.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    samples = [
        ("A_Genuine_Ramesh_9cm.png", payload(merchant["vpa"], merchant["merchant_name"], merchant["category_code"], "EXPO-A"), 9),
        ("B_Wrong_Name_9cm.png", payload(merchant["vpa"], "Ramesh Stores Altered", merchant["category_code"], "EXPO-B"), 9),
        ("C_VPA_Swap_9cm.png", payload(second["vpa"], merchant["merchant_name"], merchant["category_code"], "EXPO-C"), 9),
        ("D_Fake_VPA_9cm.png", payload("demo-fake-vpa@okaxis", "Ramesh Stores", merchant["category_code"], "EXPO-D"), 9),
        ("E_Sticker_4cm.png", payload("sticker-demo@okaxis", "Sticker Overlay Demo", transaction="EXPO-E"), 4),
    ]
    for filename, text, side_cm in samples:
        image = make_qr(text, side_cm)
        image.save(OUTPUT_DIR / filename, dpi=(DPI, DPI))
        print(f"Created {OUTPUT_DIR / filename} ({image.width}px, {side_cm} cm target)")


if __name__ == "__main__":
    main()