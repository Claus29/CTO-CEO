"""Generador PDF mínimo (texto con Helvetica), sin dependencias externas."""
from __future__ import annotations

import unicodedata
import zlib

_W_REG = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278] + [556] * 10 + \
    [278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
     667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556,
     278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334,
     260, 334, 584]
_W_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278] + [556] * 10 + \
    [333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
     667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556,
     333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389,
     280, 389, 584]

PAGE_W, PAGE_H = 595.28, 841.89
MARGIN = 48


def _char_width(ch: str, bold: bool) -> int:
    table = _W_BOLD if bold else _W_REG
    o = ord(ch)
    if 32 <= o <= 126:
        return table[o - 32]
    base = unicodedata.normalize("NFD", ch)[0]
    if 32 <= ord(base) <= 126:
        return table[ord(base) - 32]
    return 600


def text_width(text: str, size: float, bold: bool = False) -> float:
    return sum(_char_width(c, bold) for c in text) * size / 1000


def _encode(text: str) -> bytes:
    text = text.replace("→", "->").replace("—", "-").replace("–", "-").replace("·", "-") \
        .replace("…", "...").replace("“", '"').replace("”", '"').replace("‘", "'") \
        .replace("’", "'")
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


class PdfDocument:
    def __init__(self, title: str):
        self.title = title
        self.pages: list[list[bytes]] = []
        self.y = 0.0
        self._new_page()

    def _new_page(self):
        self.pages.append([])
        self.y = PAGE_H - MARGIN

    @property
    def width(self) -> float:
        return PAGE_W - 2 * MARGIN

    def _ensure(self, height: float):
        if self.y - height < MARGIN + 20:
            self._new_page()

    def _put(self, x: float, y: float, text: str, size: float, bold: bool, color=(0.1, 0.1, 0.1)):
        font = "F2" if bold else "F1"
        r, g, b = color
        self.pages[-1].append(
            b"BT %.3f %.3f %.3f rg /%s %.1f Tf %.2f %.2f Td (" % (r, g, b, font.encode(), size, x, y) +
            _encode(text) + b") Tj ET")

    def rect(self, x, y, w, h, color):
        r, g, b = color
        self.pages[-1].append(b"%.3f %.3f %.3f rg %.2f %.2f %.2f %.2f re f" % (r, g, b, x, y, w, h))

    def line(self, color=(0.8, 0.8, 0.8)):
        self._ensure(8)
        r, g, b = color
        self.pages[-1].append(b"%.3f %.3f %.3f RG 0.6 w %.2f %.2f m %.2f %.2f l S" %
                              (r, g, b, MARGIN, self.y, PAGE_W - MARGIN, self.y))
        self.y -= 8

    def wrap(self, text: str, size: float, bold: bool, width: float) -> list[str]:
        lines = []
        for para in (text or "").split("\n"):
            words = para.split(" ")
            cur = ""
            for w in words:
                cand = f"{cur} {w}" if cur else w
                if text_width(cand, size, bold) <= width:
                    cur = cand
                    continue
                if cur:
                    lines.append(cur)
                while text_width(w, size, bold) > width:
                    cut = len(w)
                    while cut > 1 and text_width(w[:cut], size, bold) > width:
                        cut -= 1
                    lines.append(w[:cut])
                    w = w[cut:]
                cur = w
            lines.append(cur)
        return lines

    def text(self, text: str, size: float = 10, bold: bool = False, color=(0.1, 0.1, 0.1), indent: float = 0,
             gap: float = 3):
        lh = size * 1.35
        for ln in self.wrap(text, size, bold, self.width - indent):
            self._ensure(lh)
            self.y -= lh
            self._put(MARGIN + indent, self.y + size * 0.25, ln, size, bold, color)
        self.y -= gap

    def label_value(self, label: str, value: str, size: float = 9.5):
        lw = 140
        lines = self.wrap(value or "—", size, False, self.width - lw)
        lh = size * 1.35
        for i, ln in enumerate(lines):
            self._ensure(lh)
            self.y -= lh
            if i == 0:
                self._put(MARGIN, self.y + size * 0.25, label, size, True, (0.35, 0.35, 0.4))
            self._put(MARGIN + lw, self.y + size * 0.25, ln, size, False)
        self.y -= 1

    def space(self, h: float):
        self.y -= h
        if self.y < MARGIN + 20:
            self._new_page()

    def heading_bar(self, text: str, color=(0.17, 0.24, 0.42)):
        self._ensure(26)
        self.y -= 22
        self.rect(MARGIN, self.y - 4, self.width, 20, color)
        self._put(MARGIN + 8, self.y + 2, text, 11, True, (1, 1, 1))
        self.y -= 8

    def output(self) -> bytes:
        objs: list[bytes] = []

        def add(obj: bytes) -> int:
            objs.append(obj)
            return len(objs)

        catalog = add(b"")
        pages_id = add(b"")
        f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        page_ids = []
        total = len(self.pages)
        for n, ops in enumerate(self.pages, 1):
            footer = b"BT 0.5 0.5 0.5 rg /F1 8 Tf %.2f %.2f Td (" % (MARGIN, 24) + \
                _encode(f"{self.title}  -  página {n} de {total}") + b") Tj ET"
            content = zlib.compress(b"\n".join(ops + [footer]))
            cid = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(content) + content + b"\nendstream")
            pid = add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /Font << /F1 %d 0 R "
                      b"/F2 %d 0 R >> >> /Contents %d 0 R >>" % (pages_id, PAGE_W, PAGE_H, f1, f2, cid))
            page_ids.append(pid)
        objs[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
        objs[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
            b" ".join(b"%d 0 R" % p for p in page_ids), len(page_ids))
        info = add(b"<< /Title (" + _encode(self.title) + b") /Producer (DEV CONTROL) >>")
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, obj in enumerate(objs, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objs) + 1, catalog, info, xref)
        return bytes(out)
