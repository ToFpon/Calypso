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


class Document:
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
