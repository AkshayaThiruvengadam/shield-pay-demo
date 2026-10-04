"""Generate and score balanced identity, location, sticker, and combined cases."""
from collections import Counter
from io import BytesIO
from urllib.parse import urlencode

import qrcode
from PIL import Image
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
)

from app import app
from merchant_store import list_merchants


LABELS = ["trusted", "suspicious", "unverifiable"]


def make_payload(vpa, name, category, suffix):
    return "upi://pay?" + urlencode({
        "pa": vpa, "pn": name, "mc": category, "tr": suffix,
        "am": "1.00", "cu": "INR",
    })


def qr_image(text, box_size=12):
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H,
                         box_size=box_size, border=4)
    code.add_data(text)
    code.make(fit=True)
    return code.make_image(fill_color="black", back_color="white").convert("RGB")


def image_bytes(image):
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def make_overlay(original_payload, sticker_payload):
    """Place a smaller second QR partly over a larger printed QR."""
    original = qr_image(original_payload, 16).resize((640, 640), Image.Resampling.NEAREST)
    sticker = qr_image(sticker_payload, 8).resize((160, 160), Image.Resampling.NEAREST)
    frame = Image.new("RGB", (720, 720), "white")
    frame.paste(original, (40, 40))
    frame.paste(sticker, (500, 500))
    return frame


def build_cases(merchants):
    cases = []
    for index, merchant in enumerate(merchants):
        original = make_payload(merchant["vpa"], merchant["merchant_name"],
                                merchant["category_code"], f"LEGIT-{index}")
        at_shop = (merchant["latitude"], merchant["longitude"])
        away = (
            merchant["latitude"] + (merchant["radius_m"] + 1000) / 111000,
            merchant["longitude"],
        )
        cases.append(("clean", "trusted", original, at_shop, qr_image(original)))

        wrong_name = make_payload(merchant["vpa"], "Different Demo Name",
                                  merchant["category_code"], f"NAME-{index}")
        cases.append(("wrong_name", "suspicious", wrong_name, at_shop, qr_image(wrong_name)))

        other = merchants[(index + 1) % len(merchants)]
        swapped = make_payload(other["vpa"], merchant["merchant_name"],
                               merchant["category_code"], f"SWAP-{index}")
        cases.append(("vpa_swap", "suspicious", swapped, at_shop, qr_image(swapped)))

        fake = make_payload(f"fake{index}@notregistered", merchant["merchant_name"],
                            merchant["category_code"], f"FAKE-{index}")
        cases.append(("fake_vpa", "unverifiable", fake, at_shop, qr_image(fake)))
        cases.append(("location_away", "suspicious", original, away, qr_image(original)))

        sticker_payload = make_payload(f"sticker{index}@notregistered", "Sticker Demo",
                                       merchant["category_code"], f"STICKER-{index}")
        overlay = make_overlay(original, sticker_payload)
        cases.append(("sticker_overlay", "suspicious", original, at_shop, overlay))

        combined = make_payload(merchant["vpa"], "Different Demo Name",
                               merchant["category_code"], f"COMBINED-{index}")
        combined_overlay = make_overlay(combined, sticker_payload)
        cases.append(("combined_attack", "suspicious", combined, away, combined_overlay))
    return cases


def run_evaluation():
    merchants = list_merchants()
    if len(merchants) < 2:
        raise SystemExit("Seed the merchant database before evaluating.")
    cases = build_cases(merchants)
    expected, predicted = [], []
    details = Counter()
    failures = []

    with app.test_client() as client:
        for attack, label, raw, coordinates, image in cases:
            response = client.post(
                "/verify",
                data={
                    "raw": raw,
                    "latitude": coordinates[0],
                    "longitude": coordinates[1],
                    "accuracy_m": 0,
                    "frame": (BytesIO(image_bytes(image)), f"{attack}.png", "image/png"),
                },
                content_type="multipart/form-data",
            )
            actual = response.get_json().get("verdict", "error") if response.is_json else "error"
            expected.append(label)
            predicted.append(actual)
            details[(attack, label == actual)] += 1
            if label != actual:
                body = response.get_json() if response.is_json else {}
                failures.append((attack, label, actual, body.get("tamper", {})))

    accuracy = accuracy_score(expected, predicted)
    precision, recall, f1, support = precision_recall_fscore_support(
        expected, predicted, labels=LABELS, average=None, zero_division=0
    )
    macro = precision_recall_fscore_support(
        expected, predicted, labels=LABELS, average="macro", zero_division=0
    )
    matrix = confusion_matrix(expected, predicted, labels=LABELS)
    print(f"Cases scored: {len(cases)}")
    print(f"Accuracy: {accuracy:.4f} ({sum(a == b for a, b in zip(expected, predicted))}/{len(cases)})")
    print("Class             Precision  Recall  F1-score  Support")
    for index, label in enumerate(LABELS):
        print(f"{label:17} {precision[index]:9.4f} {recall[index]:7.4f} {f1[index]:9.4f} {support[index]:8d}")
    print(f"Macro average     {macro[0]:9.4f} {macro[1]:7.4f} {macro[2]:9.4f}")
    print("Confusion matrix (rows=actual, columns=predicted):")
    print("actual\\pred      " + "  ".join(f"{label:13}" for label in LABELS))
    for label, row in zip(LABELS, matrix):
        print(f"{label:17}" + "  ".join(f"{value:13d}" for value in row))
    print("Per attack group:")
    for attack in sorted({case[0] for case in cases}):
        total = sum(count for (group, _), count in details.items() if group == attack)
        correct = details[(attack, True)]
        print(f"{attack:18} {correct}/{total} correct ({correct / total:.3f})")
    for attack, wanted, got, tamper in failures:
        print(f"MISCLASSIFIED {attack}: expected={wanted}, predicted={got}, tamper={tamper}")
    print("Synthetic QR results are a prototype evaluation, not real-world performance evidence.")


if __name__ == "__main__":
    run_evaluation()