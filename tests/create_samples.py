"""
Script to generate sample drawing files for manual testing.
Run: python tests/create_samples.py
"""
from __future__ import annotations
import os, sys

SAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_files")
os.makedirs(SAMPLE_DIR, exist_ok=True)


def make_dxf(filename: str, drawing_number: str, title: str = "Sample Drawing"):
    path = os.path.join(SAMPLE_DIR, filename)
    content = f"""  0
SECTION
  2
HEADER
  9
$ACADVER
  1
AC1009
  0
ENDSEC
  0
SECTION
  2
ENTITIES
  0
TEXT
  8
0
 10
50.0
 20
200.0
 30
0.0
 40
10.0
  1
図面番号: {drawing_number}
  0
TEXT
  8
0
 10
50.0
 20
180.0
 30
0.0
 40
8.0
  1
TITLE: {title}
  0
TEXT
  8
TITLEBLOCK
 10
50.0
 20
160.0
 30
0.0
 40
6.0
  1
Rev: A   Date: 2026-03-12
  0
TEXT
  8
TITLEBLOCK
 10
50.0
 20
140.0
 30
0.0
 40
6.0
  1
DRW-NO: {drawing_number}
  0
ENDSEC
  0
EOF
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"[Created] {path}")


def make_pdf(filename: str, drawing_number: str, title: str = "Sample Drawing"):
    """Create a minimal PDF with embedded text."""
    try:
        import fitz  # PyMuPDF
        path = os.path.join(SAMPLE_DIR, filename)
        doc = fitz.open()
        page = doc.new_page(width=841, height=595)  # A3 landscape

        # Title block area (bottom-right)
        page.insert_text((500, 530), f"Drawing No: {drawing_number}", fontsize=14, color=(0, 0, 0))
        page.insert_text((500, 550), f"Title: {title}",              fontsize=10, color=(0, 0, 0))
        page.insert_text((500, 565), "Rev: A   Scale: 1:1",          fontsize=8,  color=(0.3, 0.3, 0.3))
        page.insert_text((50,  50),  "CONFIDENTIAL",                  fontsize=8,  color=(0.7, 0, 0))

        # Some drawing content
        page.draw_rect(fitz.Rect(50, 80, 450, 450), color=(0, 0, 0), width=1)
        page.insert_text((200, 270), f"[Part: {drawing_number}]",    fontsize=16, color=(0, 0, 0.8))

        doc.save(path)
        doc.close()
        print(f"[Created] {path}")
    except ImportError:
        print("[SKIP] PyMuPDF not installed — skipping PDF sample generation")


def main():
    print(f"Generating sample files in: {SAMPLE_DIR}\n")
    make_dxf("DRW-001_機械部品.dxf",   "DRW-001",  "機械部品 A")
    make_dxf("DRW-002_電気回路.dxf",   "DRW-002",  "電気回路図")
    make_dxf("AB-1234_assembly.dxf",   "AB-1234",  "Assembly Drawing")
    make_dxf("XYZ-5678_detail.dxf",    "XYZ-5678", "Detail Drawing")
    make_pdf("PDF-DRW-100_layout.pdf",  "PDF-DRW-100", "Layout Plan A")
    make_pdf("PDF-AB-2000_elec.pdf",    "AB-2000",     "Electrical Drawing")
    print("\nDone. You can now index these files with:")
    print(f"  python -m src index {SAMPLE_DIR}")


if __name__ == "__main__":
    main()
