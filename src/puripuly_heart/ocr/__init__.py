"""Screen-text OCR detection (prototype).

Self-contained and OFF by default. Nothing here is imported by the main app
unless the dashboard OCR toggle is switched on, which launches
``overlay_proc`` as a separate subprocess (mirroring the VR overlay pattern).
Detection-only for now — draws bounding boxes, no translation.
"""
