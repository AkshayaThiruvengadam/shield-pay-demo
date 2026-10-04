

"""
Phase 5 — Automated ShieldPay Testing Script
=============================================
Reads QR test images from TEST_DIR, sends each to ShieldPay's /check_image
endpoint, compares the predicted verdict against the filename-derived
expected verdict, saves results.csv, and computes evaluation metrics
(accuracy, precision, recall, F1, false positive rate, confusion matrix,
and per-attack-type breakdown).
 
Expected filename convention:
    legit_001.png        -> Legitimate    -> expected: Trusted
    vpa_swap_001.png      -> VPA Swap      -> expected: Suspicious
    fake_vpa_001.png      -> Fake VPA      -> expected: Unverifiable
    wrong_name_001.png    -> Wrong Name    -> expected: Suspicious
"""
 
import os
import re
import csv
from collections import defaultdict
 
import requests
from sklearn.metrics import (
    accuracy_score,
    precision_recall_fscore_support,
    confusion_matrix,
)
 
# ── Configuration — edit these to match your setup ─────────────────────────
API_URL = "http://127.0.0.1:5000/check_image"   # your ShieldPay endpoint
TEST_DIR = "test_qrs"
SUBFOLDERS = ["legit", "tampered"]   # both scanned, results combined
RESULTS_CSV = "results.csv"
IMAGE_FIELD_NAME = "qr_image"    # multipart form field ShieldPay expects
 
LABELS = ["Trusted", "Suspicious", "Unverifiable"]
 
# filename prefix -> (attack_type, expected_verdict)
ATTACK_MAP = [
    (re.compile(r"^legit_M\d+", re.I),                  ("Legitimate",  "Trusted")),
    (re.compile(r"^suspicious_vpaSwap_M\d+", re.I),      ("VPA Swap",    "Suspicious")),
    (re.compile(r"^suspicious_nameSwap_M\d+", re.I),     ("Wrong Name",  "Suspicious")),
    (re.compile(r"^unverifiable_fakeVpa_M\d+", re.I),    ("Fake VPA",    "Unverifiable")),
]
 
 
def classify_filename(filename):
    """Return (attack_type, expected_verdict) based on filename prefix."""
    for pattern, (attack_type, expected) in ATTACK_MAP:
        if pattern.match(filename):
            return attack_type, expected
    raise ValueError(f"Unrecognized filename pattern: {filename}")
 
 
def get_predicted_verdict(image_path):
    """Send image to ShieldPay and extract the predicted verdict."""
    with open(image_path, "rb") as f:
        files = {IMAGE_FIELD_NAME: (os.path.basename(image_path), f, "image/png")}
        response = requests.post(API_URL, files=files, timeout=30)
    response.raise_for_status()
    data = response.json()
 
    if "verdict" in data:
        raw = data["verdict"]
    elif "result" in data and "verdict" in data["result"]:
        raw = data["result"]["verdict"]
    elif "prediction" in data:
        raw = data["prediction"]
    else:
        raise KeyError(f"Could not find verdict in response: {data}")
 
    # Normalize casing (e.g. "trusted" -> "Trusted") to match LABELS
    for label in LABELS:
        if raw.strip().lower() == label.lower():
            return label
    return raw  # unrecognized value, return as-is so it shows up clearly as a mismatch
 
 
def find_test_images():
    """Return a sorted list of (full_path, filename) across all subfolders."""
    found = []
    for sub in SUBFOLDERS:
        folder = os.path.join(TEST_DIR, sub)
        if not os.path.isdir(folder):
            print(f"[WARN] Subfolder not found, skipping: {folder}")
            continue
        for filename in os.listdir(folder):
            if filename.lower().endswith((".png", ".jpg", ".jpeg")):
                found.append((os.path.join(folder, filename), filename))
    return sorted(found, key=lambda pair: pair[1])
 
 
def run_tests():
    rows = []
    images = find_test_images()
 
    if not images:
        raise SystemExit(
            f"No images found under '{TEST_DIR}/' in subfolders {SUBFOLDERS}. "
            f"Check TEST_DIR and SUBFOLDERS."
        )
 
    for path, filename in images:
        try:
            attack_type, expected = classify_filename(filename)
        except ValueError as e:
            print(f"[SKIP] {e}")
            continue
 
        try:
            predicted = get_predicted_verdict(path)
        except Exception as e:
            print(f"[ERROR] {filename}: {e}")
            predicted = "ERROR"
 
        correct = 1 if predicted == expected else 0
        rows.append({
            "image_name": filename,
            "attack_type": attack_type,
            "expected_verdict": expected,
            "predicted_verdict": predicted,
            "correct": correct,
        })
        mark = "correct" if correct else "WRONG"
        print(f"{filename:25s} expected={expected:13s} predicted={predicted:13s} [{mark}]")
 
    return rows
 
 
def save_csv(rows):
    with open(RESULTS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "image_name", "attack_type", "expected_verdict",
            "predicted_verdict", "correct"
        ])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {len(rows)} results to {RESULTS_CSV}")
 
 
def compute_metrics(rows):
    # Only evaluate rows that got a real response (exclude ERROR rows from
    # the classifier's metrics, but report them separately)
    error_rows = [r for r in rows if r["predicted_verdict"] == "ERROR"]
    valid_rows = [r for r in rows if r["predicted_verdict"] != "ERROR"]
 
    y_true = [r["expected_verdict"] for r in valid_rows]
    y_pred = [r["predicted_verdict"] for r in valid_rows]
 
    if not valid_rows:
        print("\nNo valid predictions to score — every request failed. "
              "Check API_URL, IMAGE_FIELD_NAME, and that the server is running.")
        return None
 
    print("\n" + "=" * 62)
    print("OVERALL METRICS")
    print("=" * 62)
    if error_rows:
        print(f"NOTE: {len(error_rows)} image(s) failed to get a response "
              f"and were excluded from metrics: "
              f"{[r['image_name'] for r in error_rows]}")
 
    acc = accuracy_score(y_true, y_pred)
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=LABELS, average=None, zero_division=0
    )
    macro_p, macro_r, macro_f1, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=LABELS, average="macro", zero_division=0
    )
 
    print(f"\nAccuracy: {acc:.4f}  ({sum(r['correct'] for r in valid_rows)}/{len(valid_rows)})")
    print(f"\n{'Class':15s}{'Precision':>10s}{'Recall':>10s}{'F1-score':>10s}{'Support':>10s}")
    for i, label in enumerate(LABELS):
        print(f"{label:15s}{precision[i]:10.4f}{recall[i]:10.4f}{f1[i]:10.4f}{int(support[i]):10d}")
    print(f"{'Macro avg':15s}{macro_p:10.4f}{macro_r:10.4f}{macro_f1:10.4f}")
 
    # Confusion matrix
    cm = confusion_matrix(y_true, y_pred, labels=LABELS)
    print("\nConfusion Matrix (rows = actual/expected, cols = predicted)")
    print(" " * 15 + "".join(f"{l:>14s}" for l in LABELS))
    for i, label in enumerate(LABELS):
        print(f"{label:15s}" + "".join(f"{cm[i][j]:14d}" for j in range(len(LABELS))))
 
    # False Positive Rate per class, one-vs-rest
    print("\nFalse Positive Rate per class (one-vs-rest):")
    for i, label in enumerate(LABELS):
        fp = cm[:, i].sum() - cm[i][i]
        tn = cm.sum() - cm[i, :].sum() - cm[:, i].sum() + cm[i][i]
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0
        print(f"  {label:15s} FPR = {fpr:.4f}  (FP={fp}, TN={tn})")
 
    return cm
 
 
def per_attack_breakdown(rows):
    print("\n" + "=" * 62)
    print("PERFORMANCE BY ATTACK TYPE")
    print("=" * 62)
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["attack_type"]].append(r["correct"])
 
    for attack_type, results in grouped.items():
        acc = sum(results) / len(results)
        print(f"{attack_type:15s} {sum(results)}/{len(results)} correct  (accuracy={acc:.4f})")
 
 
if __name__ == "__main__":
    rows = run_tests()
    save_csv(rows)
    compute_metrics(rows)
    per_attack_breakdown(rows)
 
