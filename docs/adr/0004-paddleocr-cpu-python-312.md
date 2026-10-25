# ADR 0004: Use PaddleOCR on CPU, on Python 3.12

- Status: Accepted
- Date: 2026-10-22

## Context

Candidates tried on real night and day frames:

- **Tesseract.** Returned nothing usable on full frames and only the timestamp on crops. Not suitable for low-light
  plate text.
- **EasyOCR.** Worked on a cropped, leveled plate but weaker than PaddleOCR on the same crops (it dropped or
  confused edge characters that PaddleOCR read).
- **PaddleOCR** (PP-OCRv5 and v6 models). Read the same crops accurately, returns text boxes and polygons (used for the
  plate shape and outline), and runs on CPU.

PaddlePaddle publishes wheels for CPython 3.12 and older. A newer system Python could not install it.

## Decision

Use PaddleOCR with the PP-OCRv6 medium detector and recogniser on CPU, in a Python 3.12 virtual environment.
The `ocr` extra pins nothing beyond minimum versions. A bundled static ffmpeg (`imageio-ffmpeg`) removes the need for a
system install.

## Consequences

- No GPU needed. Per-frame cost is about 1 to 2 seconds at the chosen detector sizes.
- The OCR extras cannot install on Python 3.13 or newer ([KI-4](../known-issues.md#ki-4)).
- PaddlePaddle crashes at normal interpreter exit, so processes end with `os._exit` ([KI-3](../known-issues.md#ki-3)).
- Smaller mobile models were tested and were faster (0.67 s) but missed the plate; they are not used.

## Alternatives considered

- Tesseract and EasyOCR, as above.
- A dedicated plate-recognition library. Not evaluated: it would need its own training on this camera's view.
