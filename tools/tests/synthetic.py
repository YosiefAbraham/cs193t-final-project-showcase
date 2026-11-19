"""Builds a SYNTHETIC Gradescope-style export for tests. All data is fake."""
import os
import stat
import struct
import zipfile
import zlib


def make_pdf(text="Pantry Pilot meal planner. Code at https://github.com/example/pantry-pilot", title="Pantry Pilot Test Poster", link=None):
    """Hand-written, valid single-page PDF (with xref table)."""
    stream = ("BT /F1 18 Tf 20 60 Td (%s) Tj ET" % text.replace("(", "[").replace(")", "]")).encode("latin-1")
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 720 480]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>"
        + (b"/Annots[6 0 R]" if link else b"") + b">>",
        b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    if link:
        objs.append(b"<</Type/Annot/Subtype/Link/Rect[0 0 100 100]/A<</S/URI/URI(" + link.encode() + b")>>>>")
    info_no = len(objs) + 1
    objs.append(b"<</Title(" + title.encode("latin-1") + b")>>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<</Size %d/Root 1 0 R/Info %d 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, info_no, xref)
    return bytes(out)


def png_bytes(w=8, h=8, rgb=b"\xcc\x22\x22"):
    """Tiny valid solid-colour PNG."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    raw = b"".join(b"\x00" + rgb * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


METADATA = """\
submission_1001:
  :submitters:
  - :name: Test Student One
    :sid: '99990001'
    :email: test.one@example.edu
  - :name: Test Student Two
    :sid: '99990002'
    :email: test.two@example.edu
  :created_at: 2026-12-05 12:00:00.000000000 -08:00
  :score: 10.0
  :status: processed
submission_1002:
  :submitters:
  - :name: =HYPERLINK("http://evil","click")
    :sid: '99990003'
    :email: test.three@example.edu
  :score: 9.0
  :status: processed
  :answers:
    q1: A running coach that plans workouts for marathon training. It adapts weekly.
submission_1003:
  :submitters:
  - :name: Test Student Four
    :email: test.four@example.edu
"""


def build_export_zip(path, with_malicious=True):
    pdf1 = make_pdf(link="https://example.com/live-demo?utm_source=x&id=7")
    pdf2 = make_pdf(text="Marathon Coach with Claude Code", title="Marathon Coach")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("export/submission_metadata.yml", METADATA)
        z.writestr("export/submission_1001/poster.pdf", pdf1)
        z.writestr("export/submission_1001/links.txt",
                   "Live: https://claude.ai/artifact/abc123\nbad: http://insecure.example.com/x\n"
                   "js: javascript:alert(1)\nhttps://www.gradescope.com/courses/1/submissions/2\n")
        z.writestr("export/submission_1002.pdf", pdf2)
        z.writestr("export/submission_1003/notes.txt", "Nothing useful, no poster here.")
        if with_malicious:
            z.writestr("../evil_zipslip.txt", "pwned")
            z.writestr("/tmp/evil_abs_zipslip.txt", "pwned")
            z.writestr("export/submission_1003/..\\..\\evil_backslash.txt", "pwned")
            info = zipfile.ZipInfo("export/submission_1003/link_to_etc")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "/etc/passwd")
    return path
