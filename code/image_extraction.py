"""
Image amount extraction using EasyOCR.
The expert on turning a receipt image into a number.

Setup:
    pip install easyocr

First run downloads model weights (~100MB) -- one-time cost, not per-call
API cost, so it doesn't need to go in usage_report.md.
"""

import os
import re
import easyocr

from config import DATASET_DIR

# Initialize once, reused across calls (loading the model is slow, ~5-10s).
_READER = easyocr.Reader(["en"], gpu=False)


def extract_amount_from_image(store, event_id):
    """
    Finds the image linked to this event_id, runs OCR, and parses out the
    monetary amount. Returns a float.
    """
    img_id = None
    for img in store.images:
        if img.get("related_event_id") == event_id:
            img_id = img["image_id"]
            break
    if img_id is None:
        raise ValueError(f"No image found linked to event {event_id}")

    path = os.path.join(DATASET_DIR, "media", "images", f"{img_id}.png")

    results = _READER.readtext(path, detail=1)
    full_text = " ".join(text for (_bbox, text, _conf) in results)

    # DEBUG: uncomment while tuning against real receipts
    # print(f"[OCR:{img_id}] {full_text}")

    candidates = re.findall(r"[\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+", full_text)

    parsed = []
    for c in candidates:
        cleaned = _normalize_number(c)
        if cleaned is not None:
            parsed.append(cleaned)

    if not parsed:
        raise ValueError(f"No numeric amount found in OCR text for {path}: {full_text!r}")

    # Heuristic: the amount is usually the largest number on a receipt/confirmation
    return max(parsed)


def _normalize_number(raw: str):
    """
    Convert '1,302.40', '15.656.000', or '52100' into a float. Handles both
    comma-decimal and dot-decimal conventions.
    """
    raw = raw.strip()
    if not raw:
        return None

    if "." in raw and "," in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        tail = raw.split(",")[-1]
        if len(tail) == 2:
            raw = raw.replace(",", "", raw.count(",") - 1).replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "." in raw:
        parts = raw.split(".")
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]) and len(parts[0]) <= 3:
            raw = raw.replace(".", "")

    try:
        return float(raw)
    except ValueError:
        return None