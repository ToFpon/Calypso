"""Outils PDF (repris d'Odysseus, `odysseus/backend/pdf.py`) : compresser, fusionner, filigraner,
réorganiser les pages (ordre, rotation, suppression), extraire, éclater ; protéger par un mot de
passe et retirer la protection ; caviarder.

Règles (celles d'Odysseus) : tout est écrit sous un nom caché du dossier de destination puis posé
d'un coup ; une sortie nouvelle n'écrase JAMAIS un fichier existant (« nom (2).pdf ») ; un
remplacement voulu (« Remplacer le document ») passe par os.replace. Document protégé par un mot de
passe : une copie déchiffrée est faite dans un dossier privé (0700) et effacée après.
"""

from __future__ import annotations

import contextlib
import errno
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

from ..i18n import tr
from .jobs import Job, ToolError

GS_LEVELS = [("/screen", tr("Écran (72 ppp, le plus léger)")), ("/ebook", tr("Livre numérique (150 ppp)")),
             ("/printer", tr("Impression (300 ppp)")), ("/prepress", tr("Prépresse (couleurs préservées)"))]


def gs() -> Optional[str]:
    return shutil.which("gs") or shutil.which("ghostscript")


def qpdf() -> Optional[str]:
    return shutil.which("qpdf")


def is_pdf(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(1024).lstrip().startswith(b"%PDF")
    except OSError:
        return False


def _need_qpdf():
    if not qpdf():
        raise ToolError(tr("qpdf est introuvable (sudo apt install qpdf)"))


# ---------------------------------------------------------------------------------------------
# Fichiers
# ---------------------------------------------------------------------------------------------

def unique_name(folder: str, name: str) -> str:
    """« nom.pdf » libre dans le dossier, sinon « nom (2).pdf », « nom (3).pdf »…"""
    if not os.path.lexists(os.path.join(folder, name)):
        return name
    base, ext = os.path.splitext(name)
    m = re.match(r"^(.*) \((\d+)\)$", base)
    if m:
        base = m.group(1)
    k = 2
    while os.path.lexists(os.path.join(folder, f"{base} ({k}){ext}")):
        k += 1
    return f"{base} ({k}){ext}"


def suggest_output(path: str, suffix: str) -> str:
    """« dossier/nom (suffixe).pdf », libre."""
    folder = os.path.dirname(os.path.abspath(path))
    base = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(folder, unique_name(folder, f"{base} ({suffix}).pdf"))


def _tmp_near(dst: str, suffix: str = ".pdf") -> str:
    fd, tmp = tempfile.mkstemp(suffix=suffix, prefix=".calypso-", dir=os.path.dirname(os.path.abspath(dst)))
    os.close(fd)
    return tmp


def _sync(path: str):
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _mode_like(path: str, like: Optional[str]):
    """mkstemp crée en 0600 : droits du fichier remplacé, sinon ceux d'un fichier ordinaire."""
    try:
        if like and os.path.exists(like):
            os.chmod(path, os.stat(like).st_mode & 0o7777)
        else:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(path, 0o666 & ~umask)
    except OSError:
        pass


def place_new(tmp: str, dst: str) -> str:
    """Pose tmp sous le nom dst sans jamais écraser (lien dur puis retrait du nom caché : le nom
    ne peut pas être pris entre la vérification et la pose). Renvoie le chemin final."""
    _mode_like(tmp, None)
    _sync(tmp)
    folder = os.path.dirname(os.path.abspath(dst))
    for _attempt in range(50):
        try:
            os.link(tmp, dst)
            os.unlink(tmp)
            return dst
        except FileExistsError:
            dst = os.path.join(folder, unique_name(folder, os.path.basename(dst)))
        except OSError as e:
            if e.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV):
                raise
            # système de fichiers sans liens durs (FAT, certains partages) : vérifier puis renommer
            if os.path.lexists(dst):
                dst = os.path.join(folder, unique_name(folder, os.path.basename(dst)))
                continue
            os.rename(tmp, dst)
            return dst
    raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), dst)


def replace_into(tmp: str, dst: str):
    """Remplacement voulu (« Remplacer le document ») : droits gardés, mise sur disque, d'un coup."""
    _mode_like(tmp, dst)
    _sync(tmp)
    os.replace(tmp, dst)
    _sync(os.path.dirname(os.path.abspath(dst)))


def _finish(tmp: str, dst: str, replace: bool) -> str:
    if replace:
        replace_into(tmp, dst)
        return dst
    return place_new(tmp, dst)


def _cleanup(*paths):
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


@contextlib.contextmanager
def plain(job: Job, src: str, password: str = ""):
    """Chemin d'une version lisible sans mot de passe : src lui-même, ou une copie déchiffrée dans
    un dossier privé, effacée à la sortie."""
    if not password:
        yield src
        return
    _need_qpdf()
    folder = tempfile.mkdtemp(prefix="calypso-")          # 0700
    out = os.path.join(folder, "doc.pdf")
    try:
        _run(job, [qpdf(), f"--password={password}", "--decrypt", src, out])
        yield out
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _run(job: Job, argv: list, label: str = "", total_pages: int = 0, base=(0.0, 1.0)) -> str:
    """Lance un outil ; suit « Page N » de Ghostscript si total_pages est donné."""
    p = job.popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    out = []
    start, span = base
    for line in p.stdout:
        line = line.rstrip()
        m = re.match(r"^Page (\d+)$", line)
        if m and total_pages:
            k = int(m.group(1))
            job.progress(start + span * min(k / total_pages, 1.0),
                         (label + " — " if label else "") + tr("page {0} sur {1}").format(k, total_pages))
        elif line:
            out.append(line)
    code = p.wait()
    p.stdout.close()
    job.check_cancel()
    text = "\n".join(out)
    if code not in (0, 3):      # 3 = avertissements de qpdf
        lines = [ln for ln in out if ln.strip()]
        raise ToolError(lines[-1] if lines else tr("code {0}").format(code), text, code)
    return text


# ---------------------------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------------------------

def page_count(path: str, password: str = "") -> int:
    if qpdf():
        argv = [qpdf()] + ([f"--password={password}"] if password else []) + ["--show-npages", path]
        r = subprocess.run(argv, capture_output=True, text=True)
        if r.returncode in (0, 3) and r.stdout.strip().isdigit():
            return int(r.stdout.strip())
    from .document import Document
    return Document.open(path, password or None).n_pages


def parse_ranges(text: str, n: int) -> list[int]:
    """« 1-3, 5, 8- , z » → indices (base 0). Vide = toutes les pages. Lève ValueError."""
    text = (text or "").strip().lower()
    if not text or text in ("*", tr("tout").lower(), "all"):
        return list(range(n))
    out = []

    def num(s: str) -> int:
        s = s.strip()
        if s in ("z", "fin", "end", "last"):
            return n
        if s.startswith("r") and s[1:].isdigit():      # r1 = dernière page (syntaxe qpdf)
            return n - int(s[1:]) + 1
        try:
            v = int(s)
        except ValueError:
            raise ValueError(tr("« {0} » n'est pas un numéro de page").format(s)) from None
        if v < 1 or v > n:
            raise ValueError(tr("Page {0} hors du document ({1} pages)").format(v, n))
        return v
    for part in re.split(r"[,;\s]+", text):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = num(a) if a.strip() else 1
            end = num(b) if b.strip() else n
            step = 1 if end >= start else -1
            out.extend(range(start - 1, end - 1 + step, step))
        else:
            out.append(num(part) - 1)
    return out


def to_qpdf_ranges(indices: list[int]) -> str:
    """Indices (base 0) → « 1-3,5,7 », ordre conservé."""
    if not indices:
        return ""
    parts = []
    start = prev = indices[0]
    for i in indices[1:] + [None]:
        if i is not None and i == prev + 1:
            prev = i
            continue
        parts.append(f"{start + 1}" if start == prev else f"{start + 1}-{prev + 1}")
        if i is not None:
            start = prev = i
    return ",".join(parts)


def arrange(job: Job, src: str, order: list, dst: str, password: str = "", replace: bool = False) -> str:
    """Nouveau document fait des pages `order` = [(indice de page, rotation ajoutée)], dans cet ordre
    (pages répétées possibles, pages absentes supprimées). Une seule commande qpdf."""
    _need_qpdf()
    if not order:
        raise ToolError(tr("Impossible de supprimer toutes les pages"))
    tmp = _tmp_near(dst)
    try:
        with plain(job, src, password) as path:
            argv = [qpdf(), "--empty", "--pages", path, ",".join(str(i + 1) for i, _r in order), "--"]
            for angle in (90, 180, 270):
                pages = [k + 1 for k, (_i, r) in enumerate(order) if r % 360 == angle]
                if pages:
                    argv.append(f"--rotate=+{angle}:" + ",".join(map(str, pages)))
            argv.append(tmp)
            job.progress(0.2, tr("Mise en place de {0} pages…").format(len(order)))
            _run(job, argv)
        return _finish(tmp, dst, replace)
    except BaseException:
        _cleanup(tmp)
        raise


def split_pages(job: Job, src: str, out_dir: str, every: int = 1, password: str = "") -> list:
    """Éclate le document : un fichier toutes les `every` pages, jamais par-dessus un fichier."""
    n = page_count(src, password)
    base = os.path.splitext(os.path.basename(src))[0]
    width = len(str(n))
    out = []
    with plain(job, src, password) as path:
        for start in range(0, n, every):
            job.check_cancel()
            end = min(start + every, n)
            name = f"{base}-{start + 1:0{width}d}" + (f"-{end:0{width}d}" if end - start > 1 else "") + ".pdf"
            dst = os.path.join(out_dir, name)
            job.progress(start / max(n, 1), name)
            out.append(arrange(job, path, [(i, 0) for i in range(start, end)], dst))
    return out


# ---------------------------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------------------------

@dataclass
class CompressResult:
    dst: str
    before: int
    after: int
    kept_original: bool = False


def compress(job: Job, src: str, dst: str, level: str = "/ebook", grayscale: bool = False,
             password: str = "") -> CompressResult:
    exe = gs()
    if not exe:
        raise ToolError(tr("Ghostscript est introuvable (sudo apt install ghostscript)"))
    before = os.path.getsize(src)
    n = page_count(src, password)
    tmp = _tmp_near(dst)
    try:
        with plain(job, src, password) as path:
            argv = [exe, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5", f"-dPDFSETTINGS={level}",
                    "-dNOPAUSE", "-dBATCH", "-dDetectDuplicateImages=true", "-dCompressFonts=true",
                    f"-sOutputFile={tmp}"]
            if grayscale:
                argv += ["-sColorConversionStrategy=Gray", "-dProcessColorModel=/DeviceGray"]
            argv.append(path)
            _run(job, argv, os.path.basename(src), n)
        after = os.path.getsize(tmp)
        if after >= before and not grayscale:
            # plus gros (ou pareil) : le document était déjà optimisé, on ne produit rien
            _cleanup(tmp)
            return CompressResult("", before, before, kept_original=True)
        return CompressResult(place_new(tmp, dst), before, after)
    except BaseException:
        _cleanup(tmp)
        raise


# ---------------------------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------------------------

def merge(job: Job, items: list, dst: str) -> str:
    """items : (fichier, pages « 1-3, 5 » — vide = tout, mot de passe)."""
    _need_qpdf()
    if not items:
        raise ToolError(tr("Aucun fichier à fusionner"))
    tmp = _tmp_near(dst)
    try:
        with contextlib.ExitStack() as stack:
            argv = [qpdf(), "--empty", "--pages"]
            for path, ranges, password in items:
                readable = stack.enter_context(plain(job, path, password))
                n = page_count(readable)
                try:
                    idx = parse_ranges(ranges, n)
                except ValueError as e:
                    raise ToolError(f"{os.path.basename(path)} : {e}") from None
                if not idx:
                    raise ToolError(tr("{0} : aucune page choisie").format(os.path.basename(path)))
                argv += [readable, to_qpdf_ranges(idx)]
            argv += ["--", tmp]
            job.progress(0.2, tr("Fusion de {0} fichiers…").format(len(items)))
            _run(job, argv)
        return place_new(tmp, dst)
    except BaseException:
        _cleanup(tmp)
        raise


# ---------------------------------------------------------------------------------------------
# Filigrane (dessiné par cairo, posé par qpdf --overlay)
# ---------------------------------------------------------------------------------------------

POSITIONS = ["center", "tiled", "top-left", "top-right", "bottom-left", "bottom-right", "top", "bottom"]


@dataclass
class WatermarkOptions:
    text: str = ""
    image: str = ""                 # chemin d'une image (PNG, JPEG, SVG…) à la place du texte
    font: str = "Sans Bold"
    size: float = 60.0              # points (texte) ; pour une image : % de la largeur de page
    color: tuple = (0.8, 0.0, 0.0)
    opacity: float = 0.3
    angle: float = 45.0
    position: str = "center"
    pages: str = ""                 # vide = toutes
    flatten: bool = False
    dpi: int = 150
    margin: float = 36.0


def draw_text(ctx, o: WatermarkOptions, w: float, h: float):
    import gi
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo
    layout = PangoCairo.create_layout(ctx)
    desc = Pango.FontDescription.from_string(o.font)
    desc.set_absolute_size(o.size * Pango.SCALE)
    layout.set_font_description(desc)
    layout.set_text(o.text, -1)
    _ink, logical = layout.get_pixel_extents()
    tw, th = logical.width, logical.height
    r, g, b = o.color
    ctx.set_source_rgba(r, g, b, o.opacity)

    def stamp(cx, cy):
        ctx.save()
        ctx.translate(cx, cy)
        ctx.rotate(-math.radians(o.angle))
        ctx.translate(-tw / 2, -th / 2)
        PangoCairo.show_layout(ctx, layout)
        ctx.restore()
    _place(o, w, h, tw, th, stamp)


def draw_image(ctx, o: WatermarkOptions, w: float, h: float, cache: dict):
    import gi
    gi.require_version("Gdk", "4.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf
    pb = cache.get("pb")
    if pb is None:
        pb = cache["pb"] = GdkPixbuf.Pixbuf.new_from_file(o.image)
    iw, ih = pb.get_width(), pb.get_height()
    s = w * max(1.0, min(100.0, o.size)) / 100.0 / iw
    tw, th = iw * s, ih * s

    def stamp(cx, cy):
        ctx.save()
        ctx.translate(cx, cy)
        ctx.rotate(-math.radians(o.angle))
        ctx.translate(-tw / 2, -th / 2)
        ctx.scale(s, s)
        Gdk.cairo_set_source_pixbuf(ctx, pb, 0, 0)
        ctx.paint_with_alpha(o.opacity)
        ctx.restore()
    _place(o, w, h, tw, th, stamp)


def _place(o: WatermarkOptions, w, h, tw, th, stamp):
    rad = math.radians(o.angle)
    bw = abs(tw * math.cos(rad)) + abs(th * math.sin(rad))     # boîte englobante après rotation
    bh = abs(tw * math.sin(rad)) + abs(th * math.cos(rad))
    m = o.margin
    pos = o.position
    if pos == "tiled":
        step_x, step_y = max(bw * 1.25, 60), max(bh * 1.6, 60)
        row = 0
        y = -bh / 2
        while y < h + bh:
            x = (step_x / 2 if row % 2 else 0) - bw / 2
            while x < w + bw:
                stamp(x, y)
                x += step_x
            y += step_y
            row += 1
        return
    cx = {"top-left": m + bw / 2, "bottom-left": m + bw / 2, "top-right": w - m - bw / 2,
          "bottom-right": w - m - bw / 2}.get(pos, w / 2)
    cy = {"top-left": m + bh / 2, "top-right": m + bh / 2, "top": m + bh / 2,
          "bottom-left": h - m - bh / 2, "bottom-right": h - m - bh / 2, "bottom": h - m - bh / 2}.get(pos, h / 2)
    stamp(cx, cy)


def _sizes(path: str) -> list:
    from .document import Document
    d = Document.open(path)
    return [d.size(i) for i in range(d.n_pages)]


def watermark(job: Job, src: str, dst: str, o: WatermarkOptions, password: str = "") -> str:
    import cairo
    _need_qpdf()
    if not o.text.strip() and not o.image:
        raise ToolError(tr("Indiquez un texte ou une image"))
    wm, tmp = _tmp_near(dst), _tmp_near(dst)
    flat = _tmp_near(dst) if o.flatten else None
    try:
        with plain(job, src, password) as path:
            sizes = _sizes(path)
            n = len(sizes)
            try:
                selected = set(parse_ranges(o.pages, n))
            except ValueError as e:
                raise ToolError(str(e)) from None
            job.progress(0.05, tr("Dessin du filigrane…"))
            surf = cairo.PDFSurface(wm, sizes[0][0], sizes[0][1])
            ctx = cairo.Context(surf)
            cache: dict = {}
            for i, (w, h) in enumerate(sizes):
                surf.set_size(w, h)
                if i in selected:
                    (draw_image(ctx, o, w, h, cache) if o.image else draw_text(ctx, o, w, h))
                ctx.show_page()
            surf.finish()
            job.check_cancel()
            job.progress(0.3, tr("Application sur {0} pages…").format(n))
            _run(job, [qpdf(), path, "--overlay", wm, "--", tmp])
        if o.flatten:
            exe = gs()
            if not exe:
                raise ToolError(tr("L'aplatissement demande Ghostscript (sudo apt install ghostscript)"))
            _run(job, [exe, "-sDEVICE=pdfimage24", f"-r{int(o.dpi)}", "-sCompression=JPEG", "-dJPEGQ=90",
                       "-dNOPAUSE", "-dBATCH", f"-sOutputFile={flat}", tmp], tr("Aplatissement"), n, (0.5, 0.5))
            return place_new(flat, dst)
        return place_new(tmp, dst)
    finally:
        _cleanup(wm, tmp, flat)


# ---------------------------------------------------------------------------------------------
# Mot de passe
# ---------------------------------------------------------------------------------------------

def protect(job: Job, src: str, dst: str, user_pw: str, owner_pw: str = "", allow_print: bool = True,
            allow_copy: bool = True, allow_modify: bool = True, password: str = "", replace: bool = False) -> str:
    """Chiffrement AES-256 (qpdf) : mot de passe d'ouverture, et droits (impression, copie,
    modification) protégés par le mot de passe propriétaire (aléatoire s'il n'est pas donné)."""
    _need_qpdf()
    if not user_pw:
        raise ToolError(tr("Indiquez un mot de passe"))
    if not owner_pw:
        import secrets
        owner_pw = secrets.token_urlsafe(24)
    tmp = _tmp_near(dst)
    try:
        with plain(job, src, password) as path:
            argv = [qpdf(), "--encrypt", user_pw, owner_pw, "256",
                    f"--print={'full' if allow_print else 'none'}",
                    f"--extract={'y' if allow_copy else 'n'}",
                    f"--modify={'all' if allow_modify else 'none'}", "--", path, tmp]
            job.progress(0.3, tr("Chiffrement…"))
            _run(job, argv)
        return _finish(tmp, dst, replace)
    except BaseException:
        _cleanup(tmp)
        raise


def unprotect(job: Job, src: str, dst: str, password: str, replace: bool = False) -> str:
    _need_qpdf()
    tmp = _tmp_near(dst)
    try:
        _run(job, [qpdf(), f"--password={password}", "--decrypt", src, tmp])
        return _finish(tmp, dst, replace)
    except BaseException:
        _cleanup(tmp)
        raise


def generate_password(length: int = 20) -> str:
    """Repris de Pressoir : minuscules, majuscules, chiffres, symboles, chaque sorte présente."""
    import secrets
    import string
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits, "!#$%&*+-=?@^_~.,:;"]
    alphabet = "".join(pools)
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(max(length, 4)))
        if all(any(c in p for c in pw) for p in pools):
            return pw


def password_strength(pw: str) -> tuple[float, str]:
    """Repris de Pressoir : entropie estimée en bits et appréciation."""
    if not pw:
        return 0.0, tr("Aucun")
    pool = 0
    if any(c.islower() for c in pw):
        pool += 26
    if any(c.isupper() for c in pw):
        pool += 26
    if any(c.isdigit() for c in pw):
        pool += 10
    if any(not c.isalnum() for c in pw):
        pool += 32
    bits = len(pw) * math.log2(pool or 1)
    label = tr("Faible") if bits < 40 else tr("Moyen") if bits < 64 else tr("Fort") if bits < 100 else tr("Très fort")
    return bits, label


# ---------------------------------------------------------------------------------------------
# Caviardage
# ---------------------------------------------------------------------------------------------

def redact(job: Job, src: str, boxes: dict, dst: str, dpi: int = 200, password: str = "") -> str:
    """Masque définitivement les zones `boxes` = {page: [(x, y, l, h) en points, origine en haut]}.
    Chaque page touchée est redessinée en IMAGE (zones peintes en noir) : le texte, les images et
    les annotations qui étaient dessous n'existent plus dans le fichier. Les autres pages restent
    telles quelles."""
    import cairo
    from .document import Document
    _need_qpdf()
    pages = sorted(p for p, rects in boxes.items() if rects)
    if not pages:
        raise ToolError(tr("Aucune zone à caviarder"))
    folder = tempfile.mkdtemp(prefix="calypso-")
    tmp = _tmp_near(dst)
    try:
        with plain(job, src, password) as path:
            doc = Document.open(path)
            n = doc.n_pages
            images = {}
            for k, page in enumerate(pages):
                job.check_cancel()
                job.progress(0.8 * k / len(pages), tr("Page {0}").format(page + 1))
                w, h = doc.size(page)
                scale = dpi / 72
                data, pw, ph, stride = doc.render(page, scale, 0, (0, 0, int(w * scale), int(h * scale)))
                img = cairo.ImageSurface.create_for_data(bytearray(data), cairo.FORMAT_ARGB32, pw, ph, stride)
                ctx = cairo.Context(img)
                ctx.set_source_rgb(0, 0, 0)
                for (x, y, bw, bh) in boxes[page]:
                    ctx.rectangle(x * scale, y * scale, bw * scale, bh * scale)
                ctx.fill()
                img.flush()
                out = os.path.join(folder, f"p{page}.pdf")
                surf = cairo.PDFSurface(out, w, h)
                pctx = cairo.Context(surf)
                pctx.scale(1 / scale, 1 / scale)
                pctx.set_source_surface(img, 0, 0)
                pctx.paint()
                surf.finish()
                images[page] = out
            argv = [qpdf(), "--empty", "--pages"]
            run = []

            def flush():
                if run:
                    argv.extend([path, to_qpdf_ranges(run)])
                    run.clear()
            for i in range(n):
                if i in images:
                    flush()
                    argv.extend([images[i], "1"])
                else:
                    run.append(i)
            flush()
            argv += ["--", tmp]
            job.progress(0.9, tr("Assemblage…"))
            _run(job, argv)
        return place_new(tmp, dst)
    except BaseException:
        _cleanup(tmp)
        raise
    finally:
        shutil.rmtree(folder, ignore_errors=True)
