"""
OCR fallback using EasyOCR.

Lazy-loads the EasyOCR reader on first call so we don't pay the model-load
cost when a run finishes without needing any OCR.

Includes smart-hybrid rotation handling: tries rotation 0° first, then re-tries
90/180/270 if the initial result looks like garbage (indicating a rotated page).
"""
import io

_reader = None
_render_dpi = 200
ROTATION_RETRY_QUALITY_THRESHOLD = 0.3  # if OCR at 0° scores below this, try rotations


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        print("  Initializing EasyOCR reader (one-time, ~6s)...")
        _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _reader


def _quick_score(text):
    """
    Fast heuristic — real-word ratio without spellchecker overhead.
    Used only to decide whether to try rotation. Not our main quality metric.
    """
    if not text or len(text.strip()) < 20:
        return 0.0
    tokens = [t.lower().strip(".,!?;:()[]\"'-—") for t in text.split()]
    tokens = [t for t in tokens if 2 <= len(t) <= 20 and t.replace("-", "").replace("'", "").isalpha()]
    if not tokens:
        return 0.0
    # Fraction of tokens that look word-like (have at least one vowel and no crazy
    # consonant clusters). Vowel presence is a decent proxy for "real word."
    vowels = set("aeiouy")
    has_vowel = sum(1 for t in tokens if any(c in vowels for c in t))
    return has_vowel / len(tokens)


def _render_and_ocr(page, dpi, rotation, ocr_reader):
    """Render a fitz page at given rotation and OCR it. Returns joined text."""
    import fitz
    import numpy as np
    from PIL import Image

    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    if rotation:
        img = img.rotate(rotation, expand=True)
    arr = np.array(img)
    lines = ocr_reader.readtext(arr, detail=0, paragraph=True)
    return "\n".join(lines)


def ocr_pages_from_pdf(pdf_path, page_nums, dpi=200):
    """
    OCR specific pages (1-indexed) of a PDF using EasyOCR.
    Smart-hybrid rotation: try 0° first, retry 90/180/270 if result looks
    like garbage (page probably rotated in the source PDF).
    Returns {page_num: text} dict.
    """
    import fitz

    reader = _get_reader()
    doc = fitz.open(pdf_path)
    results = {}

    for pn in page_nums:
        try:
            page = doc.load_page(pn - 1)  # fitz is 0-indexed

            # Try upright first
            best_text = _render_and_ocr(page, dpi, 0, reader)
            best_score = _quick_score(best_text)

            # If output looks like garbage, try rotations
            if best_score < ROTATION_RETRY_QUALITY_THRESHOLD:
                for rot in (90, 180, 270):
                    alt_text = _render_and_ocr(page, dpi, rot, reader)
                    alt_score = _quick_score(alt_text)
                    if alt_score > best_score:
                        best_text = alt_text
                        best_score = alt_score
                if best_score >= ROTATION_RETRY_QUALITY_THRESHOLD:
                    print(f"    ↻ page {pn}: rotation fix improved score to {best_score:.2f}")

            results[pn] = best_text
        except Exception as e:
            print(f"    ! OCR failed on page {pn}: {e}")
            results[pn] = ""

    doc.close()
    return results
