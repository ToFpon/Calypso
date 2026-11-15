"""Documents PDF d'essai, faits par cairo (pas de fichier binaire dans le dépôt).

`make_book(path)` : 12 pages A4 avec titres, paragraphes, une image, des liens (vers une page, vers le
Web), un sommaire à deux niveaux. `make_mixed(path)` : pages de tailles différentes (A4, paysage,
Lettre US). `make_encrypted(src, dst, password)` : par qpdf.
"""

from __future__ import annotations

import math
import subprocess

import cairo

WORDS = ("la mer vineuse porte le navire vers une île lointaine où la nymphe chante en tissant "
         "une toile d'or tandis que les vents du soir couchent les hautes herbes près de la grotte "
         "et que les oiseaux de mer reviennent au rivage avant la nuit").split()


def _para(ctx, x, y, width, n_words, seed):
    ctx.set_font_size(11)
    words = [WORDS[(seed * 7 + k * 3) % len(WORDS)] for k in range(n_words)]
    line = ""
    for w in words:
        test = (line + " " + w).strip()
        if ctx.text_extents(test).x_advance > width:
            ctx.move_to(x, y)
            ctx.show_text(line)
            y += 15
            line = w
        else:
            line = test
    if line:
        ctx.move_to(x, y)
        ctx.show_text(line)
        y += 15
    return y + 8


def make_book(path: str, pages: int = 12):
    s = cairo.PDFSurface(path, 595, 842)
    s.set_metadata(cairo.PDF_METADATA_TITLE, "Le chant de Calypso")
    s.set_metadata(cairo.PDF_METADATA_AUTHOR, "Homère")
    ctx = cairo.Context(s)
    ctx.select_font_face("DejaVu Sans")
    chapter_ids = []
    for p in range(pages):
        ctx.set_source_rgb(0, 0, 0)
        if p % 4 == 0:
            ch = p // 4 + 1
            ctx.set_font_size(26)
            ctx.move_to(64, 90)
            ctx.show_text(f"Chapitre {ch}")
            cid = s.add_outline(cairo.PDF_OUTLINE_ROOT, f"Chapitre {ch}", f"page={p + 1}", 0)
            chapter_ids.append(cid)
            y = 130
        else:
            y = 80
        if p % 4 == 2:
            ctx.set_font_size(16)
            ctx.move_to(64, y)
            ctx.show_text(f"Section {p // 4 + 1}.2")
            s.add_outline(chapter_ids[-1], f"Section {p // 4 + 1}.2", f"page={p + 1} pos=[64 {y - 16}]", 0)
            y += 30
        for k in range(6):
            y = _para(ctx, 64, y, 467, 60, p * 10 + k)
        if p == 1:
            # image (motif de vagues) : « Enregistrer l'image sous… »
            img = cairo.ImageSurface(cairo.FORMAT_RGB24, 200, 120)
            ic = cairo.Context(img)
            for row in range(120):
                ic.set_source_rgb(0.1, 0.4 + 0.4 * math.sin(row / 9) ** 2, 0.6)
                ic.rectangle(0, row, 200, 1)
                ic.fill()
            ctx.save()
            ctx.translate(64, y)
            ctx.set_source_surface(img, 0, 0)
            ctx.paint()
            ctx.restore()
            y += 130
        if p == 0:
            ctx.set_font_size(11)
            ctx.set_source_rgb(0.1, 0.3, 0.8)
            target = min(6, pages)
            ctx.tag_begin(cairo.TAG_LINK, f"page={target} pos=[64 80]")
            ctx.move_to(64, 760)
            ctx.show_text(f"Aller au chapitre 2, page {target}")
            ctx.tag_end(cairo.TAG_LINK)
            ctx.tag_begin(cairo.TAG_LINK, "uri='https://example.org/calypso'")
            ctx.move_to(64, 780)
            ctx.show_text("Site de l'auteur")
            ctx.tag_end(cairo.TAG_LINK)
        ctx.set_source_rgb(0.4, 0.4, 0.4)
        ctx.set_font_size(9)
        ctx.move_to(290, 820)
        ctx.show_text(str(p + 1))
        s.show_page()
    s.finish()


def make_mixed(path: str):
    s = cairo.PDFSurface(path, 595, 842)
    ctx = cairo.Context(s)
    for w, h, label in ((595, 842, "A4 portrait"), (842, 595, "A4 paysage"), (612, 792, "Lettre US")):
        s.set_size(w, h)
        ctx.set_font_size(30)
        ctx.move_to(60, 100)
        ctx.show_text(label)
        s.show_page()
    s.finish()


def make_encrypted(src: str, dst: str, password: str):
    subprocess.run(["qpdf", "--encrypt", password, password + "-owner", "256", "--", src, dst], check=True)
