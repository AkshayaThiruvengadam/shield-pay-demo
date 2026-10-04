"""
Phase 3: Generate legitimate and tampered UPI QR test cases from merchants.csv.
Run this after Phase 2 (merchants.csv) and before Phase 5 (testing).

Outputs PNG QR images into test_qrs/legit/ and test_qrs/tampered/
"""
import csv
import os
import random

import qrcode

MERCHANTS_CSV = "merchants.csv"
OUT_DIR = "test_qrs"

random.seed(42)


def build_upi_string(pa, pn, mc, tr, amount="100.00", cu="INR"):
    return f"upi://pay?pa={pa}&pn={pn.replace(' ', '%20')}&mc={mc}&tr={tr}&am={amount}&cu={cu}"


def save_qr(data, path):
    img = qrcode.make(data)
    img.save(path)


def load_merchants():
    with open(MERCHANTS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    merchants = load_merchants()
    os.makedirs(f"{OUT_DIR}/legit", exist_ok=True)
    os.makedirs(f"{OUT_DIR}/tampered", exist_ok=True)

    # --- Legitimate cases: payload matches DB exactly ---
    for i, m in enumerate(merchants):
        data = build_upi_string(m["vpa"], m["merchant_name"], m["category_code"], f"TXN{1000+i}")
        save_qr(data, f"{OUT_DIR}/legit/legit_{m['merchant_id']}.png")

    # --- Tampered cases ---
    other_names = [m["merchant_name"] for m in merchants]
    fake_vpas = [f"fraudster{i}@okaxis" for i in range(10)]

    for i, m in enumerate(merchants):
        # Case 1: VPA swapped to a different registered merchant's VPA, name kept same
        # (classic "suspicious" case -> name mismatch against DB)
        other = random.choice([x for x in merchants if x != m])
        data = build_upi_string(other["vpa"], m["merchant_name"], m["category_code"], f"TXN{2000+i}")
        save_qr(data, f"{OUT_DIR}/tampered/suspicious_vpaSwap_{m['merchant_id']}.png")

        # Case 2: Completely unregistered/fake VPA (classic "unverifiable" case)
        fake_vpa = random.choice(fake_vpas)
        data = build_upi_string(fake_vpa, m["merchant_name"], m["category_code"], f"TXN{3000+i}")
        save_qr(data, f"{OUT_DIR}/tampered/unverifiable_fakeVpa_{m['merchant_id']}.png")

        # Case 3: Valid VPA, but display name altered (subtle spoof)
        wrong_name = random.choice([n for n in other_names if n != m["merchant_name"]])
        data = build_upi_string(m["vpa"], wrong_name, m["category_code"], f"TXN{4000+i}")
        save_qr(data, f"{OUT_DIR}/tampered/suspicious_nameSwap_{m['merchant_id']}.png")

    print(f"Generated {len(merchants)} legit QR codes.")
    print(f"Generated {len(merchants) * 3} tampered QR codes (3 attack types x {len(merchants)} merchants).")


if __name__ == "__main__":
    main()