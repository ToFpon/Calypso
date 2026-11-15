"""Document PDF lu par Poppler (la bibliothèque de Papers et d'Evince).

Poppler n'accepte pas deux appels simultanés sur un même document : tout passe par `Document.lock`
(rendu dans le fil de rendu, recherche dans son fil, texte et liens dans le fil principal).
Coordonnées : celles de la page sans rotation, origine en HAUT à gauche, en points (1/72 de pouce).
Poppler donne les liens et les résultats de recherche avec l'origine en BAS : convertis ici.
"""

from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

import cairo
import gi

gi.require_version("Poppler", "0.18")
from gi.repository import GLib, Poppler  # noqa: E402


class PasswordNeeded(Exception):
    """Document chiffré : un mot de passe est demandé (ou celui donné est faux)."""

    def __init__(self, wrong: bool):
        super().__init__("password")
        self.wrong = wrong


class OpenError(Exception):
    pass


@dataclass
class Link:
    """Destination d'un lien, d'une entrée du sommaire ou d'un résultat."""
    kind: str                          # page, uri, file, named, none
    page: int = -1                     # page de destination (0 = première)
    top: Optional[float] = None        # hauteur visée dans la page (points, depuis le haut)
    left: Optional[float] = None
    uri: str = ""                      # adresse web, ou fichier pour « file »
    name: str = ""                     # action nommée (NextPage, PrevPage, FirstPage, LastPage…)


@dataclass
class OutlineItem:
    title: str
    link: Link
    children: list = field(default_factory=list)
    expanded: bool = False


def _rect_top_left(r, height: float) -> tuple[float, float, float, float]:
    """Rectangle de Poppler (origine en bas) → (x, y, l, h) origine en haut."""
    x1, x2 = min(r.x1, r.x2), max(r.x1, r.x2)
    y1, y2 = min(r.y1, r.y2), max(r.y1, r.y2)
    return (x1, height - y2, x2 - x1, y2 - y1)


def _text(value) -> str:
    return value if isinstance(value, str) else ""


# ==================================================================================================
# Annotations et formulaires
# ==================================================================================================

ANNOT_KINDS = {
    Poppler.AnnotType.TEXT: "note", Poppler.AnnotType.HIGHLIGHT: "highlight",
    Poppler.AnnotType.UNDERLINE: "underline", Poppler.AnnotType.STRIKE_OUT: "strikeout",
    Poppler.AnnotType.SQUIGGLY: "squiggly", Poppler.AnnotType.SQUARE: "square",
    Poppler.AnnotType.CIRCLE: "circle", Poppler.AnnotType.LINE: "line", Poppler.AnnotType.FREE_TEXT: "freetext",
    Poppler.AnnotType.INK: "ink", Poppler.AnnotType.STAMP: "stamp", Poppler.AnnotType.POLYGON: "polygon",
    Poppler.AnnotType.POLY_LINE: "polyline", Poppler.AnnotType.CARET: "caret",
    Poppler.AnnotType.FILE_ATTACHMENT: "attachment", Poppler.AnnotType.SOUND: "sound",
}
# jamais montrées comme annotations : liens, champs de formulaire, bulles rattachées
HIDDEN_ANNOTS = {Poppler.AnnotType.LINK, Poppler.AnnotType.WIDGET, Poppler.AnnotType.POPUP}
MARKUP_KINDS = ("highlight", "underline", "strikeout", "squiggly")


@dataclass
class AnnotInfo:
    page: int
    rect: tuple                    # (x, y, l, h), origine en haut
    kind: str
    annot: object                  # Poppler.Annot
    contents: str = ""
    author: str = ""
    color: Optional[tuple] = None  # (r, g, b) de 0 à 1
    modified: str = ""


@dataclass
class FieldInfo:
    page: int
    rect: tuple
    kind: str                      # text, multiline, password, check, radio, push, combo, list, signature
    field: object                  # Poppler.FormField
    name: str = ""
    read_only: bool = False


def _pcolor(rgb) -> Poppler.Color:
    c = Poppler.Color()
    c.red, c.green, c.blue = (max(0, min(65535, int(round(v * 65535)))) for v in rgb)
    return c


def _prect(x1, y1, x2, y2) -> Poppler.Rectangle:
    r = Poppler.Rectangle()
    r.x1, r.y1, r.x2, r.y2 = x1, y1, x2, y2
    return r


def _quad(x1, y1, x2, y2) -> Poppler.Quadrilateral:
    """Quadrilatère d'un rectangle (origine en bas)."""
    q = Poppler.Quadrilateral()
    # q.p1 renvoie une COPIE du point : on pose des points neufs (sinon QuadPoints tout à 0, rien
    # de dessiné — vu sur le premier essai)
    for name, (px, py) in (("p1", (x1, y2)), ("p2", (x2, y2)), ("p3", (x1, y1)), ("p4", (x2, y1))):
        pt = Poppler.Point()
        pt.x, pt.y = px, py
        setattr(q, name, pt)
    return q


def default_author() -> str:
    return (GLib.get_real_name() or "").strip() or GLib.get_user_name() or ""


class _AnnotsMixin:
    """Méthodes ajoutées à Document (même verrou)."""

    def _annot_info(self, i: int, m) -> Optional[AnnotInfo]:
        a = m.annot
        t = a.get_annot_type()
        if t in HIDDEN_ANNOTS:
            return None
        h = self.sizes[i][1]
        color = a.get_color()
        info = AnnotInfo(i, _rect_top_left(m.area, h), ANNOT_KINDS.get(t, "other"), a,
                         contents=_text(a.get_contents()),
                         color=(color.red / 65535, color.green / 65535, color.blue / 65535) if color else None,
                         modified=_text(a.get_modified()))
        if isinstance(a, Poppler.AnnotMarkup):
            info.author = _text(a.get_label())
        return info

    def annots(self, i: int) -> list:
        cached = self._annots.get(i)
        if cached is None:
            with self.lock:
                cached = [x for x in (self._annot_info(i, m) for m in self._pages[i].get_annot_mapping() or [])
                          if x is not None]
            self._annots[i] = cached
        return cached

    def annot_at(self, i: int, x: float, y: float, slack: float = 2.0) -> Optional[AnnotInfo]:
        for info in reversed(self.annots(i)):                 # la dernière posée est dessus
            ax, ay, aw, ah = info.rect
            if ax - slack <= x <= ax + aw + slack and ay - slack <= y <= ay + ah + slack:
                return info
        return None

    def find_annot(self, annot) -> Optional[AnnotInfo]:
        """AnnotInfo d'une annotation. Poppler crée un NOUVEL objet Python à chaque lecture des
        annotations d'une page (et ne leur donne pas de nom) : on compare le type et le rectangle."""
        with self.lock:
            try:
                page = annot.get_page_index()
                kind = annot.get_annot_type()
                r = annot.get_rectangle()
            except Exception:
                return None
        if not (0 <= page < self.n_pages):
            return None
        for info in self.annots(page):
            if info.annot is annot:
                return info
        for info in self.annots(page):
            with self.lock:
                q = info.annot.get_rectangle()
                same = info.annot.get_annot_type() == kind
            if same and all(abs(a - b) < 0.01 for a, b in ((q.x1, r.x1), (q.y1, r.y1), (q.x2, r.x2), (q.y2, r.y2))):
                return info
        return None

    def _changed(self, i: int):
        self._annots.pop(i, None)
        self.dirty = True

    def _stamp(self, a, color, author):
        if color is not None:
            a.set_color(_pcolor(color))
        if author and isinstance(a, Poppler.AnnotMarkup):
            a.set_label(author)

    def add_markup(self, i: int, kind: str, rects: list, color, author: str = ""):
        """Surligner / souligner / barrer / onduler les rectangles donnés (lignes de la sélection)."""
        if not rects:
            return None
        h = self.sizes[i][1]
        quads = []
        x1 = y1 = float("inf")
        x2 = y2 = float("-inf")
        for (x, y, w, hh) in rects:
            top, bottom = h - y, h - y - hh                 # origine en bas pour Poppler
            quads.append(_quad(x, bottom, x + w, top))
            x1, x2 = min(x1, x), max(x2, x + w)
            y1, y2 = min(y1, bottom), max(y2, top)
        make = {"highlight": Poppler.AnnotTextMarkup.new_highlight,
                "underline": Poppler.AnnotTextMarkup.new_underline,
                "strikeout": Poppler.AnnotTextMarkup.new_strikeout,
                "squiggly": Poppler.AnnotTextMarkup.new_squiggly}[kind]
        with self.lock:
            a = make(self.doc, _prect(x1, y1, x2, y2), quads)
            self._stamp(a, color, author)
            self._pages[i].add_annot(a)
            self._quads[self._annot_key(a)] = quads
        self._changed(i)
        return a

    def add_note(self, i: int, x: float, y: float, text: str, color, author: str = ""):
        h = self.sizes[i][1]
        with self.lock:
            a = Poppler.AnnotText.new(self.doc, _prect(x, h - y - 24, x + 24, h - y))
            a.set_icon("Note")
            a.set_contents(text or "")
            self._stamp(a, color, author)
            self._pages[i].add_annot(a)
        self._changed(i)
        return a

    def add_shape(self, i: int, kind: str, rect, color, author: str = ""):
        x, y, w, hh = rect
        h = self.sizes[i][1]
        r = _prect(x, h - y - hh, x + w, h - y)
        with self.lock:
            a = (Poppler.AnnotCircle if kind == "circle" else Poppler.AnnotSquare).new(self.doc, r)
            self._stamp(a, color, author)
            self._pages[i].add_annot(a)
        self._changed(i)
        return a

    def remove_annot(self, i: int, annot):
        with self.lock:
            self._pages[i].remove_annot(annot)
        self._changed(i)

    def restore_annot(self, i: int, annot):
        """Remet une annotation retirée (annuler une suppression). Poppler 24.02 n'ÉCRIT PAS une
        annotation retirée puis rajoutée telle quelle (son objet est marqué libre) : on en pose une
        copie neuve, renvoyée (l'appelant garde celle-là). Types qu'on ne sait pas recréer : remise
        telle quelle (visible, mais perdue à l'enregistrement)."""
        with self.lock:
            fresh = self._clone(annot)
            self._pages[i].add_annot(fresh or annot)
        self._changed(i)
        return fresh or annot

    @staticmethod
    def _annot_key(a):
        r = a.get_rectangle()
        return (int(a.get_annot_type()), round(r.x1, 2), round(r.y1, 2), round(r.x2, 2), round(r.y2, 2))

    def _clone(self, a):
        t = a.get_annot_type()
        r = a.get_rectangle()
        rect = _prect(r.x1, r.y1, r.x2, r.y2)
        if t == Poppler.AnnotType.TEXT:
            b = Poppler.AnnotText.new(self.doc, rect)
            b.set_icon(a.get_icon() or "Note")
        elif t in (Poppler.AnnotType.HIGHLIGHT, Poppler.AnnotType.UNDERLINE, Poppler.AnnotType.STRIKE_OUT,
                   Poppler.AnnotType.SQUIGGLY):
            make = {Poppler.AnnotType.HIGHLIGHT: Poppler.AnnotTextMarkup.new_highlight,
                    Poppler.AnnotType.UNDERLINE: Poppler.AnnotTextMarkup.new_underline,
                    Poppler.AnnotType.STRIKE_OUT: Poppler.AnnotTextMarkup.new_strikeout,
                    Poppler.AnnotType.SQUIGGLY: Poppler.AnnotTextMarkup.new_squiggly}[t]
            # jamais get_quadrilaterals : PyGObject libère mal le tableau (« free(): invalid pointer »)
            quads = self._quads.get(self._annot_key(a))
            b = make(self.doc, rect, quads or [_quad(r.x1, r.y1, r.x2, r.y2)])
        elif t in (Poppler.AnnotType.SQUARE, Poppler.AnnotType.CIRCLE):
            b = (Poppler.AnnotSquare if t == Poppler.AnnotType.SQUARE else Poppler.AnnotCircle).new(self.doc, rect)
            inside = a.get_interior_color()
            if inside is not None:
                b.set_interior_color(inside)
        else:
            return None
        color = a.get_color()
        if color is not None:
            b.set_color(color)
        b.set_contents(a.get_contents() or "")
        if isinstance(a, Poppler.AnnotMarkup):
            if a.get_label():
                b.set_label(a.get_label())
            b.set_opacity(a.get_opacity())
        return b

    def annot_props(self, annot) -> dict:
        with self.lock:
            c = annot.get_color()
            return {"contents": _text(annot.get_contents()),
                    "color": (c.red / 65535, c.green / 65535, c.blue / 65535) if c else None}

    def set_annot_props(self, i: int, annot, props: dict):
        with self.lock:
            if "contents" in props:
                annot.set_contents(props["contents"] or "")
            if props.get("color") is not None:
                annot.set_color(_pcolor(props["color"]))
        self._changed(i)

    # -- formulaires ------------------------------------------------------------------------------
    def fields(self, i: int) -> list:
        cached = self._fields.get(i)
        if cached is None:
            h = self.sizes[i][1]
            cached = []
            with self.lock:
                for m in self._pages[i].get_form_field_mapping() or []:
                    f = m.field
                    t = f.get_field_type()
                    if t == Poppler.FormFieldType.TEXT:
                        tt = f.text_get_text_type()
                        kind = "password" if f.text_is_password() else \
                            "multiline" if tt == Poppler.FormTextType.MULTILINE else \
                            "file" if tt == Poppler.FormTextType.FILE_SELECT else "text"
                    elif t == Poppler.FormFieldType.BUTTON:
                        bt = f.button_get_button_type()
                        kind = {Poppler.FormButtonType.CHECK: "check", Poppler.FormButtonType.RADIO: "radio"}.get(
                            bt, "push")
                    elif t == Poppler.FormFieldType.CHOICE:
                        kind = "combo" if f.choice_get_choice_type() == Poppler.FormChoiceType.COMBO else "list"
                    elif t == Poppler.FormFieldType.SIGNATURE:
                        kind = "signature"
                    else:
                        continue
                    cached.append(FieldInfo(i, _rect_top_left(m.area, h), kind, f, _text(f.get_name()),
                                            bool(f.is_read_only())))
            self._fields[i] = cached
        return cached

    def field_at(self, i: int, x: float, y: float) -> Optional[FieldInfo]:
        for info in self.fields(i):
            fx, fy, fw, fh = info.rect
            if fx <= x <= fx + fw and fy <= y <= fy + fh:
                return info
        return None

    def has_fields(self, limit: int = 400) -> bool:
        return any(self.fields(i) for i in range(min(self.n_pages, limit)))

    def field_value(self, info: FieldInfo):
        f = info.field
        with self.lock:
            if info.kind in ("text", "multiline", "password", "file"):
                return _text(f.text_get_text())
            if info.kind in ("check", "radio"):
                return bool(f.button_get_state())
            if info.kind in ("combo", "list"):
                sel = [k for k in range(f.choice_get_n_items()) if f.choice_is_item_selected(k)]
                text = _text(f.choice_get_text()) if f.choice_is_editable() else ""
                return (tuple(sel), text)
        return None

    def choice_items(self, info: FieldInfo) -> list:
        with self.lock:
            return [_text(info.field.choice_get_item(k)) for k in range(info.field.choice_get_n_items())]

    def set_field_value(self, info: FieldInfo, value):
        f = info.field
        with self.lock:
            if info.kind in ("text", "multiline", "password", "file"):
                f.text_set_text(value or "")
            elif info.kind in ("check", "radio"):
                f.button_set_state(bool(value))
            elif info.kind in ("combo", "list"):
                sel, text = value
                f.choice_unselect_all()
                for k in sel:
                    f.choice_select_item(k)
                if text and f.choice_is_editable() and not sel:
                    f.choice_set_text(text)
        # une case radio change aussi les autres du groupe : toute la page est relue
        self._fields.pop(info.page, None)
        self._touched[(info.page, info.rect, info.name)] = (info, value)
        self.dirty = True

    # -- enregistrement ----------------------------------------------------------------------------
    def save(self, dst: str):
        """Écrit le document (annotations et champs compris) sous un nom caché du dossier de
        destination, le met sur disque, puis le pose d'un coup (os.replace) : jamais de fichier à
        moitié écrit, l'ancien reste si ça échoue. Les droits d'un fichier remplacé sont gardés."""
        folder = os.path.dirname(os.path.abspath(dst)) or "."
        tmp = os.path.join(folder, f".{os.path.basename(dst)}.calypso-{os.getpid()}.tmp")
        try:
            with self.lock:
                ok = self.doc.save(GLib.filename_to_uri(os.path.abspath(tmp), None))
            if not ok or not os.path.exists(tmp):
                raise OSError("save failed")
            if any(info.kind in ("check", "radio") for info, _v in self._touched.values()):
                from .formfix import repair
                repair(tmp, self.path)
            fd = os.open(tmp, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.chmod(tmp, os.stat(dst).st_mode & 0o7777)
            except OSError:
                pass
            os.replace(tmp, dst)
            try:
                dfd = os.open(folder, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass
        except GLib.Error as e:
            _unlink(tmp)
            raise OSError(e.message) from None
        except BaseException:
            _unlink(tmp)
            raise
        if os.path.abspath(dst) == os.path.abspath(self.path):
            self.dirty = False


def _unlink(path: str):
    try:
        os.unlink(path)
    except OSError:
        pass


class Document(_AnnotsMixin):
    def __init__(self, path: str, doc: Poppler.Document, password: str = ""):
        self.path = path
        self.password = password
        self.doc = doc
        self.lock = threading.RLock()
        self.n_pages = doc.get_n_pages()
        self._pages = [doc.get_page(i) for i in range(self.n_pages)]
        self.sizes = [p.get_size() for p in self._pages]           # (largeur, hauteur) en points
        self.labels = [_text(p.props.label) or str(i + 1) for i, p in enumerate(self._pages)]
        self._text: dict[int, tuple] = {}
        self._links: dict[int, list] = {}
        self._images: dict[int, list] = {}
        self._annots: dict[int, list] = {}
        self._fields: dict[int, list] = {}
        self._touched: dict = {}
        self._quads: dict = {}               # surlignages posés par nous : leurs quadrilatères (copie)             # champs remplis : (page, rect, nom) → (FieldInfo, valeur)
        self.dirty = False                   # annotations ou champs changés depuis l'ouverture
        self.title = _text(doc.props.title).strip()

    # -- ouverture ------------------------------------------------------------------------------
    @classmethod
    def open(cls, path: str, password: Optional[str] = None) -> "Document":
        uri = GLib.filename_to_uri(os.path.abspath(path), None)
        try:
            doc = Poppler.Document.new_from_file(uri, password)
        except GLib.Error as e:
            if e.domain == "poppler-quark" and e.code == int(Poppler.Error.ENCRYPTED):
                raise PasswordNeeded(wrong=bool(password)) from None
            raise OpenError(e.message) from None
        if doc.get_n_pages() <= 0:
            raise OpenError("no pages")
        return cls(path, doc, password or "")

    # -- pages ------------------------------------------------------------------------------------
    def size(self, i: int, rotation: int = 0) -> tuple[float, float]:
        w, h = self.sizes[i]
        return (h, w) if rotation % 180 else (w, h)

    def page_label(self, i: int) -> str:
        return self.labels[i] if 0 <= i < self.n_pages else ""

    def find_label(self, text: str) -> int:
        """Numéro tapé dans la barre : étiquette de la page (« iv », « A-3 ») ou numéro (1 = première)."""
        text = text.strip()
        if not text:
            return -1
        for i, label in enumerate(self.labels):
            if label == text:
                return i
        low = text.lower()
        for i, label in enumerate(self.labels):
            if label.lower() == low:
                return i
        if text.isdigit():
            n = int(text) - 1
            return n if 0 <= n < self.n_pages else -1
        return -1

    def render(self, i: int, scale: float, rotation: int, tile: tuple[int, int, int, int]):
        """Rendu d'une partie de la page (tile = x, y, l, h en pixels de la page tournée et agrandie)
        sur fond blanc. Renvoie (octets, largeur, hauteur, pas) au format de cairo (ARGB32)."""
        tx, ty, tw, th = tile
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, tw), max(1, th))
        ctx = cairo.Context(surface)
        ctx.set_source_rgb(1, 1, 1)
        ctx.paint()
        ctx.translate(-tx, -ty)
        ctx.scale(scale, scale)
        w, h = self.sizes[i]
        rotation %= 360
        if rotation == 90:
            ctx.translate(h, 0)
        elif rotation == 180:
            ctx.translate(w, h)
        elif rotation == 270:
            ctx.translate(0, w)
        if rotation:
            ctx.rotate(math.radians(rotation))
        with self.lock:
            self._pages[i].render(ctx)
        surface.flush()
        return bytes(surface.get_data()), surface.get_width(), surface.get_height(), surface.get_stride()

    # -- texte ----------------------------------------------------------------------------------
    def text(self, i: int) -> tuple[str, list]:
        """Texte de la page et rectangle de chaque caractère (x1, y1, x2, y2, origine en haut)."""
        cached = self._text.get(i)
        if cached is None:
            with self.lock:
                page = self._pages[i]
                text = _text(page.get_text())
                ok, rects = page.get_text_layout()
            boxes = [(r.x1, r.y1, r.x2, r.y2) for r in (rects or [])] if ok else []
            if len(boxes) != len(text):         # ne devrait pas arriver : pas de rectangles plutôt que faux
                boxes = []
            cached = (text, boxes)
            self._text[i] = cached
        return cached

    def selection(self, i: int, x1: float, y1: float, x2: float, y2: float, style: str = "glyph"):
        """Sélection de Poppler entre deux points (ordre de lecture, comme Evince) : rectangles et texte."""
        st = {"word": Poppler.SelectionStyle.WORD, "line": Poppler.SelectionStyle.LINE}.get(
            style, Poppler.SelectionStyle.GLYPH)
        r = Poppler.Rectangle()
        r.x1, r.y1, r.x2, r.y2 = x1, y1, x2, y2
        with self.lock:
            page = self._pages[i]
            region = page.get_selected_region(1.0, st, r)
            text = _text(page.get_selected_text(st, r))
        rects = []
        if region is not None:
            for k in range(region.num_rectangles()):
                q = region.get_rectangle(k)
                rects.append((q.x, q.y, q.width, q.height))
        return rects, text

    def char_at(self, i: int, x: float, y: float) -> bool:
        """Vrai si le point tombe sur du texte (curseur en I)."""
        _text_, boxes = self.text(i)
        for (a, b, c, d) in boxes:
            if a <= x <= c and b <= y <= d:
                return True
        return False

    def find(self, i: int, query: str, case: bool = False, whole: bool = False) -> list:
        flags = Poppler.FindFlags.MULTILINE | Poppler.FindFlags.IGNORE_DIACRITICS
        if case:
            flags |= Poppler.FindFlags.CASE_SENSITIVE
        if whole:
            flags |= Poppler.FindFlags.WHOLE_WORDS_ONLY
        h = self.sizes[i][1]
        with self.lock:
            found = self._pages[i].find_text_with_options(query, flags) or []
        return [_rect_top_left(r, h) for r in found]

    def context(self, i: int, rect, span: int = 40) -> tuple[str, str, str]:
        """Texte autour d'un résultat (avant, trouvé, après) pour la liste des résultats."""
        text, boxes = self.text(i)
        if not boxes:
            return ("", "", "")
        x, y, w, h = rect
        cy = y + h / 2
        hits = [k for k, (a, b, c, d) in enumerate(boxes)
                if b <= cy <= d and c >= x and a <= x + w and not text[k].isspace()]
        if not hits:
            return ("", "", "")
        start, end = max(0, hits[0] - span), min(len(text), hits[-1] + 1 + span)
        before = text[start:hits[0]].replace("\n", " ")
        match = text[hits[0]:hits[-1] + 1].replace("\n", " ")
        after = text[hits[-1] + 1:end].replace("\n", " ")
        return before, match, after

    # -- liens --------------------------------------------------------------------------------
    def links(self, i: int) -> list:
        cached = self._links.get(i)
        if cached is None:
            h = self.sizes[i][1]
            cached = []
            with self.lock:
                for m in self._pages[i].get_link_mapping() or []:
                    link = self._link(m.action)
                    if link.kind != "none":
                        cached.append((_rect_top_left(m.area, h), link))
            self._links[i] = cached
        return cached

    def link_at(self, i: int, x: float, y: float) -> Optional[Link]:
        for (lx, ly, lw, lh), link in self.links(i):
            if lx <= x <= lx + lw and ly <= y <= ly + lh:
                return link
        return None

    def _dest(self, dest) -> Link:
        if dest is None:
            return Link("none")
        if dest.type == Poppler.DestType.NAMED:
            with self.lock:
                found = self.doc.find_dest(dest.named_dest)
            if found is None or found.type == Poppler.DestType.NAMED:
                return Link("none")
            dest = found
        page = max(0, min(self.n_pages - 1, dest.page_num - 1))
        h = self.sizes[page][1]
        top = left = None
        if dest.type in (Poppler.DestType.XYZ, Poppler.DestType.FITH, Poppler.DestType.FITBH,
                         Poppler.DestType.FITR):
            if dest.change_top:
                top = max(0.0, min(h, h - dest.top))
        if dest.type in (Poppler.DestType.XYZ, Poppler.DestType.FITV, Poppler.DestType.FITBV,
                         Poppler.DestType.FITR) and dest.change_left:
            left = max(0.0, dest.left)
        return Link("page", page=page, top=top, left=left)

    def _link(self, action) -> Link:
        if action is None:
            return Link("none")
        kind = action.type
        if kind == Poppler.ActionType.GOTO_DEST:
            return self._dest(action.goto_dest.dest)
        if kind == Poppler.ActionType.URI:
            uri = _text(action.uri.uri)
            return Link("uri", uri=uri) if uri else Link("none")
        if kind == Poppler.ActionType.GOTO_REMOTE:
            name = _text(action.goto_remote.file_name)
            if not name:
                return Link("none")
            path = name if os.path.isabs(name) else os.path.join(os.path.dirname(self.path), name)
            d = action.goto_remote.dest
            page = (d.page_num - 1) if d is not None and d.type != Poppler.DestType.NAMED else 0
            return Link("file", uri=path, page=max(0, page))
        if kind == Poppler.ActionType.LAUNCH:
            name = _text(action.launch.file_name)
            if not name:
                return Link("none")
            path = name if os.path.isabs(name) else os.path.join(os.path.dirname(self.path), name)
            return Link("file", uri=path, page=0)
        if kind == Poppler.ActionType.NAMED:
            return Link("named", name=_text(action.named.named_dest))
        return Link("none")

    def resolve_named(self, name: str, current: int) -> int:
        return {"NextPage": current + 1, "PrevPage": current - 1, "FirstPage": 0,
                "LastPage": self.n_pages - 1}.get(name, -1)

    # -- images ---------------------------------------------------------------------------------
    def images(self, i: int) -> list:
        cached = self._images.get(i)
        if cached is None:
            h = self.sizes[i][1]
            with self.lock:
                cached = [(_rect_top_left(m.area, h), m.image_id)
                          for m in self._pages[i].get_image_mapping() or []]
            self._images[i] = cached
        return cached

    def image_at(self, i: int, x: float, y: float) -> Optional[int]:
        for (ix, iy, iw, ih), image_id in self.images(i):
            if ix <= x <= ix + iw and iy <= y <= iy + ih:
                return image_id
        return None

    def image_surface(self, i: int, image_id: int):
        with self.lock:
            return self._pages[i].get_image(image_id)

    # -- sommaire -----------------------------------------------------------------------------
    def outline(self) -> list:
        try:
            with self.lock:
                it = Poppler.IndexIter.new(self.doc)
        except TypeError:                   # pas de sommaire : le constructeur renvoie NULL
            return []
        if it is None:
            return []
        return self._walk(it)

    def _walk(self, it) -> list:
        items = []
        while True:
            with self.lock:
                action = it.get_action()
                is_open = it.is_open()
                child = it.get_child()
            title = ""
            link = Link("none")
            if action is not None:
                if action.type == Poppler.ActionType.GOTO_DEST:
                    title = _text(action.goto_dest.title)
                elif action.type == Poppler.ActionType.URI:
                    title = _text(action.uri.title)
                elif action.type == Poppler.ActionType.GOTO_REMOTE:
                    title = _text(action.goto_remote.title)
                elif action.type == Poppler.ActionType.NAMED:
                    title = _text(action.named.title)
                elif action.type == Poppler.ActionType.LAUNCH:
                    title = _text(action.launch.title)
                link = self._link(action)
            item = OutlineItem(title.strip(), link, expanded=bool(is_open))
            if child is not None:
                item.children = self._walk(child)
            items.append(item)
            with self.lock:
                more = it.next()
            if not more:
                break
        return items

    # -- informations ---------------------------------------------------------------------------
    def info(self) -> dict:
        d = self.doc
        with self.lock:
            return {
                "title": _text(d.props.title), "author": _text(d.props.author),
                "subject": _text(d.props.subject), "keywords": _text(d.props.keywords),
                "creator": _text(d.props.creator), "producer": _text(d.props.producer),
                "created": d.props.creation_date, "modified": d.props.mod_date,
                "format": _text(d.props.format), "linearized": bool(d.props.linearized),
                "pages": self.n_pages,
            }
