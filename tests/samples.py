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


def make_form(path: str):
    """Formulaire écrit à la main (AcroForm, NeedAppearances) : champ texte « nom », champ multiligne
    « remarques », case à cocher « accepte », boutons radio « taille » (S / M), liste déroulante « pays »."""
    objs = {}
    objs[1] = "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [4 0 R 5 0 R 6 0 R 7 0 R 10 0 R] " \
              "/NeedAppearances true /DA (/Helv 12 Tf 0 g) /DR << /Font << /Helv 11 0 R >> >> >> >>"
    objs[2] = "<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    objs[3] = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 12 0 R " \
              "/Resources << /Font << /F1 11 0 R >> >> /Annots [4 0 R 5 0 R 6 0 R 8 0 R 9 0 R 10 0 R] >>"
    objs[4] = "<< /Type /Annot /Subtype /Widget /FT /Tx /T (nom) /Rect [150 740 400 760] /P 3 0 R " \
              "/DA (/Helv 12 Tf 0 g) /F 4 /V () >>"
    objs[5] = "<< /Type /Annot /Subtype /Widget /FT /Tx /T (remarques) /Ff 4096 /Rect [150 640 400 720] " \
              "/P 3 0 R /DA (/Helv 10 Tf 0 g) /F 4 /V () >>"
    objs[6] = "<< /Type /Annot /Subtype /Widget /FT /Btn /T (accepte) /Rect [150 600 166 616] /P 3 0 R " \
              "/V /Off /AS /Off /F 4 /MK << /CA (4) >> " \
              "/AP << /N << /Oui 13 0 R /Off 14 0 R >> >> >>"
    objs[7] = "<< /FT /Btn /Ff 49152 /T (taille) /V /Off /Kids [8 0 R 9 0 R] >>"
    objs[8] = "<< /Type /Annot /Subtype /Widget /Parent 7 0 R /Rect [150 560 166 576] /P 3 0 R /AS /Off " \
              "/F 4 /MK << /CA (l) >> /AP << /N << /S 13 0 R /Off 14 0 R >> >> >>"
    objs[9] = "<< /Type /Annot /Subtype /Widget /Parent 7 0 R /Rect [200 560 216 576] /P 3 0 R /AS /Off " \
              "/F 4 /MK << /CA (l) >> /AP << /N << /M 13 0 R /Off 14 0 R >> >> >>"
    objs[10] = "<< /Type /Annot /Subtype /Widget /FT /Ch /Ff 131072 /T (pays) /Rect [150 500 300 520] " \
               "/P 3 0 R /Opt [(France) (Belgique) (Suisse)] /V (France) /DA (/Helv 12 Tf 0 g) /F 4 >>"
    objs[11] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    text = ("BT /F1 12 Tf 60 745 Td (Nom :) Tj 0 -100 Td (Remarques :) Tj 0 -40 Td (J'accepte) Tj "
            "0 -40 Td (Taille : S / M) Tj 0 -60 Td (Pays :) Tj ET")
    objs[12] = f"<< /Length {len(text)} >>\nstream\n{text}\nendstream"
    on = "0 g 3 3 10 10 re f"
    objs[13] = f"<< /Type /XObject /Subtype /Form /BBox [0 0 16 16] /Length {len(on)} >>\nstream\n{on}\nendstream"
    objs[14] = "<< /Type /XObject /Subtype /Form /BBox [0 0 16 16] /Length 0 >>\nstream\n\nendstream"
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for n in sorted(objs):
        offsets[n] = len(out)
        out += f"{n} 0 obj\n{objs[n]}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for n in sorted(objs):
        out += f"{offsets[n]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    with open(path, "wb") as fh:
        fh.write(out)


def make_nss(folder: str, name: str = "Tof Calypso") -> str:
    """Base NSS jetable avec un certificat auto-signé (pour signer dans les tests) ; renvoie aussi
    le .p12 correspondant (mot de passe « secret »)."""
    import os
    os.makedirs(folder, exist_ok=True)
    nss = os.path.join(folder, "nss")
    os.makedirs(nss, exist_ok=True)
    subprocess.run(["certutil", "-N", "-d", "sql:" + nss, "--empty-password"], check=True, capture_output=True)
    key, crt, p12 = (os.path.join(folder, n) for n in ("k.pem", "c.pem", "c.p12"))
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt,
                    "-days", "365", "-subj", f"/CN={name}/O=Essai"], check=True, capture_output=True)
    subprocess.run(["openssl", "pkcs12", "-export", "-inkey", key, "-in", crt, "-out", p12, "-passout",
                    "pass:secret", "-name", "tof"], check=True, capture_output=True)
    return nss, p12
