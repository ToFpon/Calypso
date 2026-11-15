"""Vue des pages : défilement continu ou page par page, page simple ou double, zoom, rotation,
mode nuit, sélection du texte, liens, résultats de recherche.

Widget maison qui implémente Gtk.Scrollable (posé dans un Gtk.ScrolledWindow) : il ne dessine que
ce qui est visible, quelle que soit la longueur du document. Les pages sont rendues par le fil de
rendu (`backend/render.py`) : une page entière jusqu'à 4 Mpx, au-delà des tuiles de 1024 px (seules
les tuiles visibles), plus un aperçu léger de chaque page (0,3 Mpx) montré étiré pendant le
défilement rapide ou le zoom, en attendant le rendu net.

Coordonnées : « doc » = pixels logiques du document entier mis en page ; « page » = points de la
page sans rotation, origine en haut à gauche (celles de `backend/document.py`).
"""

from __future__ import annotations

import math
import warnings
from collections import OrderedDict
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from .. import features  # noqa: E402
from ..backend.document import Document, Link  # noqa: E402
from ..backend.render import Renderer  # noqa: E402

MARGIN = 12
GAP = 12
MIN_ZOOM = 0.05
MAX_ZOOM = 16.0
PX_PER_PT = 96 / 72          # 100 % = taille réelle à 96 ppp, comme Papers
WHOLE_PAGE_PX = 4_000_000    # au-delà : tuiles
TILE = 1024
PREVIEW_PX = 300_000
CACHE_BYTES = 320 * 1024 * 1024
PREVIEWS = 120
MARKUP_TOOLS = ("highlight", "underline", "strikeout", "squiggly")
REDACT = Gdk.RGBA()
REDACT.parse("rgba(0,0,0,0.85)")
ZOOM_STEPS = (0.25, 0.33, 0.5, 0.67, 0.75, 0.85, 1.0, 1.15, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0,
              6.4, 8.0, 10.0, 12.8, 16.0)

WHITE = Gdk.RGBA()
WHITE.parse("#ffffff")
SHADOW = Gdk.RGBA()
SHADOW.parse("rgba(0,0,0,0.28)")
FOUND = Gdk.RGBA()
FOUND.parse("rgba(255,200,0,0.38)")
FOUND_CURRENT = Gdk.RGBA()
FOUND_CURRENT.parse("rgba(255,120,0,0.55)")


def _invert():
    m = Graphene.Matrix()
    m.init_from_float([-1, 0, 0, 0, 0, -1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1])
    v = Graphene.Vec4()
    v.init(1, 1, 1, 0)
    return m, v


INVERT = _invert()


def _rect(x, y, w, h) -> Graphene.Rect:
    r = Graphene.Rect()
    r.init(x, y, w, h)
    return r


class DocView(Gtk.Widget, Gtk.Scrollable):
    __gtype_name__ = "CalypsoDocView"
    __gsignals__ = {
        "page-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "zoom-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "layout-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "external-link": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "context-menu": (GObject.SignalFlags.RUN_FIRST, None, (float, float, object)),
        "history-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "page-invalidated": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "redactions-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "area-drawn": (GObject.SignalFlags.RUN_FIRST, None, (int, object)),
    }
    hadjustment = GObject.Property(type=Gtk.Adjustment)
    vadjustment = GObject.Property(type=Gtk.Adjustment)
    hscroll_policy = GObject.Property(type=Gtk.ScrollablePolicy, default=Gtk.ScrollablePolicy.MINIMUM)
    vscroll_policy = GObject.Property(type=Gtk.ScrollablePolicy, default=Gtk.ScrollablePolicy.MINIMUM)

    def __init__(self):
        super().__init__(focusable=True)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("calypso-view")
        self.document: Optional[Document] = None
        self.renderer: Optional[Renderer] = None
        self.thumb_listener = None              # fonction(clé, texture) pour les miniatures
        self.zoom = 1.0                          # 1.0 = 100 %
        self.zoom_mode = "fit-width"             # fit-width, fit-page, free
        self.rotation = 0
        self.continuous = True
        self.dual = False
        self.dual_cover = False
        self.night = False
        self.page = 0
        self._rects: list = []                   # (x, y, l, h) de chaque page dans le document, ou None
        self._column: list = []
        self._doc_size = (0, 0)
        self._laid_out = False
        self._anchor = None                      # point de page à garder sous un point de la vue
        self._target = None                      # (page, haut en points, gauche) à atteindre après mise en page
        self._cache: OrderedDict = OrderedDict()
        self._stale_previews: dict = {}
        self._stale_tiles: dict = {}
        self._cache_bytes = 0
        self._previews: OrderedDict = OrderedDict()
        self._adj_handlers = {}
        self._requests_pending = False
        self._zoom_settle = 0
        self._settled = True
        # sélection
        self._sel = None                         # (début (page, x, y), fin (page, x, y), style)
        self._sel_cache: dict = {}
        self._press = None
        self._dragging = False
        self._autoscroll = 0
        self._autoscroll_dy = 0.0
        # recherche
        self.search_results: dict = {}           # page → [(x, y, l, h)]
        self.search_current = None               # (page, index)
        # historique (précédent / suivant)
        self._back: list = []
        self._forward: list = []
        # aperçu des liens
        self._hover_link = None
        self._preview_timer = 0
        self._link_popover = None                # bulle ouverte (None : fermée)
        self._link_pop = None                    # la bulle, gardée
        self._pinch_zoom = 1.0
        self._nonc_scroll = 0.0
        self._pointer_pos = None
        # annotations et formulaires
        self.tool = "select"                     # select, note, highlight, underline, strikeout, squiggly, square, circle
        self.annotator = None                    # ui/annotations.Annotator, posé par la fenêtre
        self.selected_annot = None               # AnnotInfo
        self.show_fields = True
        self._band = None                        # rectangle en cours de tracé (page, x1, y1, x2, y2)
        self.redactions: dict = {}               # zones à caviarder : page → [(x, y, l, h)]
        self._editor = None                      # (widget, page, rect) : champ de formulaire en saisie
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._on_tooltip)

        self.connect("notify::hadjustment", self._on_adj_set, "h")
        self.connect("notify::vadjustment", self._on_adj_set, "v")

        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._on_pressed)
        click.connect("released", self._on_released)
        self.add_controller(click)
        drag = Gtk.GestureDrag(button=1)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)
        pan = Gtk.GestureDrag(button=2)
        pan.connect("drag-begin", self._on_pan_begin)
        pan.connect("drag-update", self._on_pan_update)
        self.add_controller(pan)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", lambda *_: self._end_hover())
        self.add_controller(motion)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)
        pinch = Gtk.GestureZoom()
        pinch.connect("begin", lambda *_: setattr(self, "_pinch_zoom", self.zoom))
        pinch.connect("scale-changed", self._on_pinch)
        self.add_controller(pinch)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # == document ============================================================================
    def set_document(self, document: Optional[Document], state: Optional[dict] = None):
        """Nouveau document (ou le même relu : `state` remet page, zoom et disposition)."""
        if self.renderer is not None:
            self.renderer.stop()
            self.renderer = None
        self.document = document
        self._cache.clear()
        self._cache_bytes = 0
        self._previews.clear()
        self._stale_tiles.clear()
        self._stale_previews.clear()
        self._sel = None
        self._sel_cache = {}
        self.search_results = {}
        self.search_current = None
        self._back, self._forward = [], []
        self._hide_link_preview()
        self.close_editor(commit=False)
        self.selected_annot = None
        self._band = None
        self.redactions = {}
        self._laid_out = False
        self._anchor = None
        if document is not None:
            self.renderer = Renderer(document, self._deliver)
            st = state or {}
            self.zoom_mode = st.get("zoom_mode", self.zoom_mode)
            if self.zoom_mode not in ("fit-width", "fit-page", "free"):
                self.zoom_mode = "fit-width"
            self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, st.get("scale", self.zoom)))
            self.rotation = st.get("rotation", 0) % 360 // 90 * 90
            self.continuous = st.get("continuous", self.continuous)
            self.dual = st.get("dual", self.dual)
            self.dual_cover = st.get("dual_cover", self.dual_cover)
            self.night = st.get("night", self.night)
            page = max(0, min(document.n_pages - 1, st.get("page", 0)))
            self.page = page
            offset = st.get("offset")
            top = None
            if isinstance(offset, float) and 0 <= offset <= 1:
                top = offset * document.size(page, 0)[1]
            self._target = (page, top, None, True)
        self.queue_resize()
        self.queue_draw()
        self.emit("history-changed")
        self.emit("selection-changed")

    def teardown(self):
        self.close_editor(commit=False)
        if self.annotator is not None:
            self.annotator.detach()
        self._hide_link_preview()
        if self._link_pop is not None:
            self._link_pop.unparent()
            self._link_pop = None
        pop = getattr(self, "_calypso_popover", None)
        if pop is not None and pop.get_parent() is self:
            pop.unparent()
            self._calypso_popover = None
        if self.renderer is not None:
            self.renderer.stop()
            self.renderer = None
        for src in ("_zoom_settle", "_autoscroll", "_preview_timer"):
            if getattr(self, src):
                GLib.source_remove(getattr(self, src))
                setattr(self, src, 0)
        self._hide_link_preview()

    def get_state(self) -> dict:
        st = {"page": self.page, "zoom_mode": self.zoom_mode, "scale": round(self.zoom, 4),
              "rotation": self.rotation, "continuous": self.continuous, "dual": self.dual,
              "dual_cover": self.dual_cover, "night": self.night}
        r = self._rects[self.page] if self._laid_out and self.page < len(self._rects) else None
        va = self.props.vadjustment
        if r is not None and va is not None and self.rotation == 0:
            st["offset"] = round(max(0.0, min(1.0, (va.get_value() - r[1] + MARGIN) / r[3])), 4)
        return st

    # == mise en page =========================================================================
    @property
    def scale(self) -> float:
        """Pixels logiques par point."""
        return self.zoom * PX_PER_PT

    def _device_scale(self) -> float:
        native = self.get_native()
        surface = native.get_surface() if native is not None else None
        if surface is not None and hasattr(surface, "get_scale"):
            try:
                return float(surface.get_scale())
            except Exception:
                pass
        return float(self.get_scale_factor())

    def _rows(self) -> list:
        n = self.document.n_pages
        if not self.dual:
            return [[i] for i in range(n)]
        rows = []
        start = 0
        if self.dual_cover:
            rows.append([0])
            start = 1
        for i in range(start, n, 2):
            rows.append([i, i + 1] if i + 1 < n else [i])
        return rows

    def _row_of(self, page: int, rows: list) -> list:
        for row in rows:
            if page in row:
                return row
        return rows[0]

    def _fit_zoom(self, width: int, height: int, rows: list) -> float:
        doc = self.document
        pages = range(doc.n_pages) if self.continuous else self._row_of(self.page, rows)
        maxw = max(doc.size(i, self.rotation)[0] for i in pages)
        maxh = max(doc.size(i, self.rotation)[1] for i in pages)
        cols = 2 if self.dual else 1
        avail_w = max(50, width - 2 * MARGIN - (GAP if self.dual else 0))
        z = avail_w / (cols * maxw) / PX_PER_PT
        if self.zoom_mode == "fit-page":
            if not self.continuous or True:
                cur = self._row_of(self.page, rows)
                maxh = max(doc.size(i, self.rotation)[1] for i in cur)
            z = min(z, max(50, height - 2 * MARGIN) / maxh / PX_PER_PT)
        return max(MIN_ZOOM, min(MAX_ZOOM, z))

    def _layout(self, width: int, height: int):
        doc = self.document
        rows = self._rows()
        if self.zoom_mode != "free":
            new = self._fit_zoom(width, height, rows)
            if abs(new - self.zoom) > 1e-6:
                self.zoom = new
                GLib.idle_add(lambda: (self.emit("zoom-changed"), False)[1])
        s = self.scale
        sizes = [doc.size(i, self.rotation) for i in range(doc.n_pages)]
        shown_rows = rows if self.continuous else [self._row_of(self.page, rows)]
        colw = max(int(round(sizes[i][0] * s)) for row in shown_rows for i in row)
        content_w = (2 * colw + GAP) if self.dual else colw
        total_w = content_w + 2 * MARGIN
        W = max(width, total_w)
        cx = W // 2
        rects = [None] * doc.n_pages
        columns = [0] * doc.n_pages
        y = MARGIN
        if not self.continuous:
            rowh = max(int(round(sizes[i][1] * s)) for i in shown_rows[0])
            y = max(MARGIN, (height - rowh) // 2)
        for row in shown_rows:
            rowh = 0
            for k, i in enumerate(row):
                w, h = int(round(sizes[i][0] * s)), int(round(sizes[i][1] * s))
                rowh = max(rowh, h)
                if not self.dual:
                    x = cx - w // 2
                else:
                    right = (k == 1) or (self.dual_cover and i == 0)
                    x = cx + GAP // 2 if right else cx - GAP // 2 - w
                    columns[i] = 1 if right else 0
                rects[i] = (x, y, w, h)
            # pages de hauteurs différentes sur une même ligne : centrées verticalement
            for i in row:
                x, yy, w, h = rects[i]
                rects[i] = (x, yy + (rowh - h) // 2, w, h)
            y += rowh + GAP
        total_h = y - GAP + MARGIN
        if not self.continuous:
            total_h = max(total_h, height)
        self._rects = rects
        self._column = columns
        self._doc_size = (W, max(height, total_h))
        self._laid_out = True

    def do_measure(self, orientation, for_size):
        return (0, 0, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        if self.document is None:
            self._configure(width, height, (width, height))
            return
        anchor = self._anchor
        if anchor is None and self._laid_out and self._target is None:
            anchor = self._capture_anchor(0, 0)
        self._anchor = None
        self._layout(width, height)
        self._configure(width, height, self._doc_size)
        if self._target is not None:
            page, top, left, initial = self._target
            self._target = None
            self._scroll_to_page(page, top, left)
        elif anchor is not None:
            self._apply_anchor(anchor)
        self._update_page()
        self._schedule_requests()
        self._place_editor()
        self.emit("layout-changed")

    def _configure(self, width, height, size):
        W, H = size
        ha, va = self.props.hadjustment, self.props.vadjustment
        if ha is not None:
            ha.configure(max(0, min(ha.get_value(), W - width)), 0, W, width * 0.1, width * 0.9, width)
        if va is not None:
            va.configure(max(0, min(va.get_value(), H - height)), 0, H, 40, height * 0.9, height)

    def _capture_anchor(self, vx: float, vy: float):
        """Point de la page sous (vx, vy) de la vue, en fractions de la page."""
        if not self._laid_out:
            return None
        dx, dy = vx + self._hval(), vy + self._vval()
        best = None
        for i, r in enumerate(self._rects):
            if r is None:
                continue
            x, y, w, h = r
            if y - GAP <= dy <= y + h + GAP:
                d = 0 if x <= dx <= x + w else min(abs(dx - x), abs(dx - x - w))
                if best is None or d < best[0]:
                    best = (d, i, (dx - x) / w, (dy - y) / h)
        if best is None:
            return (self.page, 0.5, 0.0, vx, vy)
        return (best[1], best[2], best[3], vx, vy)

    def _apply_anchor(self, anchor):
        page, fx, fy, vx, vy = anchor
        r = self._rects[page] if 0 <= page < len(self._rects) else None
        if r is None:
            return
        x, y, w, h = r
        self._set_values(x + fx * w - vx, y + fy * h - vy)

    def _set_values(self, hv: Optional[float], vv: Optional[float]):
        ha, va = self.props.hadjustment, self.props.vadjustment
        if ha is not None and hv is not None:
            ha.set_value(max(0, min(hv, ha.get_upper() - ha.get_page_size())))
        if va is not None and vv is not None:
            va.set_value(max(0, min(vv, va.get_upper() - va.get_page_size())))

    def _hval(self) -> float:
        ha = self.props.hadjustment
        return ha.get_value() if ha is not None else 0.0

    def _vval(self) -> float:
        va = self.props.vadjustment
        return va.get_value() if va is not None else 0.0

    def _on_adj_set(self, _w, _p, which):
        adj = self.props.hadjustment if which == "h" else self.props.vadjustment
        old = self._adj_handlers.pop(which, None)
        if old is not None:
            try:
                old[0].disconnect(old[1])
            except TypeError:
                pass
        if adj is not None:
            self._adj_handlers[which] = (adj, adj.connect("value-changed", self._on_scrolled))
        self.queue_resize()

    def _on_scrolled(self, *_):
        self.queue_draw()
        if self._editor is not None:
            self.queue_allocate()
        self._update_page()
        self._schedule_requests()
        self._hide_link_preview()

    def _update_page(self):
        if not self._laid_out or self.document is None or not self.continuous:
            return
        top, bottom = self._vval(), self._vval() + self.get_height()
        best, area = self.page, -1
        for i, r in enumerate(self._rects):
            if r is None:
                continue
            seen = min(bottom, r[1] + r[3]) - max(top, r[1])
            if seen > area + 0.5:
                best, area = i, seen
            elif r[1] > bottom:
                break
        if best != self.page:
            self.page = best
            self.emit("page-changed", best)

    # == navigation ==========================================================================
    def _scroll_to_page(self, page: int, top: Optional[float] = None, left: Optional[float] = None):
        r = self._rects[page] if 0 <= page < len(self._rects) else None
        if r is None:
            return
        x, y, w, h = r
        vv = y - MARGIN
        hv = None
        if top is not None or left is not None:
            px, py = self.page_to_doc(page, left or 0.0, top or 0.0)
            if top is not None:
                vv = py - MARGIN
            if left is not None and self.zoom_mode == "free":
                hv = px - MARGIN
        if not self.continuous:
            vv = 0 if top is None else vv
        self._set_values(hv, vv)
        if page != self.page:
            self.page = page
            self.emit("page-changed", page)

    def go_to(self, page: int, top: Optional[float] = None, left: Optional[float] = None,
              remember: bool = False):
        if self.document is None:
            return
        page = max(0, min(self.document.n_pages - 1, page))
        if remember:
            self._remember()
        if not self.continuous and not self._shows(page):
            self.page = page
            self._target = (page, top, left, False)
            self.emit("page-changed", page)
            self.queue_resize()
            return
        if not self._laid_out:
            self._target = (page, top, left, False)
            return
        self._scroll_to_page(page, top, left)

    def _shows(self, page: int) -> bool:
        return 0 <= page < len(self._rects) and self._rects[page] is not None

    def _row_step(self, delta: int):
        """Ligne suivante / précédente (page par page en mode non continu, deux pages en double)."""
        rows = self._rows()
        row = self._row_of(self.page, rows)
        k = rows.index(row) + delta
        if 0 <= k < len(rows):
            self.go_to(rows[k][0])

    def next_page(self):
        self._row_step(1)

    def previous_page(self):
        self._row_step(-1)

    def first_page(self):
        self.go_to(0, remember=True)

    def last_page(self):
        if self.document is not None:
            self.go_to(self.document.n_pages - 1, remember=True)

    def follow(self, link: Link):
        if self.document is None or link is None:
            return
        if link.kind == "page":
            self.go_to(link.page, link.top, link.left, remember=True)
        elif link.kind == "named":
            target = self.document.resolve_named(link.name, self.page)
            if target >= 0:
                self.go_to(target, remember=True)
        elif link.kind in ("uri", "file"):
            self.emit("external-link", link)

    # -- historique -----------------------------------------------------------------------------
    def _position(self):
        r = self._rects[self.page] if self._laid_out and self.page < len(self._rects) else None
        top = None
        if r is not None:
            top = max(0.0, self.doc_to_page(self.page, r[0], self._vval() + MARGIN)[1])
        return (self.page, top)

    def _remember(self):
        pos = self._position()
        if not self._back or self._back[-1] != pos:
            self._back.append(pos)
            del self._back[:-100]
        self._forward.clear()
        self.emit("history-changed")

    def can_go_back(self) -> bool:
        return bool(self._back)

    def can_go_forward(self) -> bool:
        return bool(self._forward)

    def go_back(self):
        if self._back:
            self._forward.append(self._position())
            page, top = self._back.pop()
            self.go_to(page, top)
            self.emit("history-changed")

    def go_forward(self):
        if self._forward:
            self._back.append(self._position())
            page, top = self._forward.pop()
            self.go_to(page, top)
            self.emit("history-changed")

    # == zoom et disposition =================================================================
    def set_zoom(self, zoom: float, around: Optional[tuple] = None):
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if self.document is None:
            self.zoom, self.zoom_mode = zoom, "free"
            return
        if abs(zoom - self.zoom) < 1e-6 and self.zoom_mode == "free":
            return
        if around is None:
            around = (self.get_width() / 2, self.get_height() / 2)
        self._anchor = self._capture_anchor(*around)
        self.zoom = zoom
        self.zoom_mode = "free"
        self._settle_zoom()
        self.queue_resize()
        self.emit("zoom-changed")

    def _settle_zoom(self):
        """Pendant un zoom à la molette, seuls les aperçus sont redessinés ; le rendu net vient
        150 ms après le dernier cran."""
        self._settled = False
        self._stale_tiles.clear()
        if self._zoom_settle:
            GLib.source_remove(self._zoom_settle)

        def done():
            self._zoom_settle = 0
            self._settled = True
            self._schedule_requests()
            return False
        self._zoom_settle = GLib.timeout_add(150, done)

    def zoom_in(self, around=None):
        for z in ZOOM_STEPS:
            if z > self.zoom + 1e-3:
                self.set_zoom(z, around)
                return
        self.set_zoom(MAX_ZOOM, around)

    def zoom_out(self, around=None):
        for z in reversed(ZOOM_STEPS):
            if z < self.zoom - 1e-3:
                self.set_zoom(z, around)
                return
        self.set_zoom(MIN_ZOOM, around)

    def set_zoom_mode(self, mode: str):
        if mode == self.zoom_mode:
            return
        self._anchor = self._capture_anchor(0, 0)
        self.zoom_mode = mode
        self._settle_zoom()
        self.queue_resize()
        self.emit("zoom-changed")

    def _relayout_keep_page(self):
        page = self.page
        self._target = (page, None, None, False)
        self._settle_zoom()
        self.queue_resize()
        self.queue_draw()

    def set_layout(self, continuous=None, dual=None, dual_cover=None):
        if continuous is not None:
            self.continuous = continuous
        if dual is not None:
            self.dual = dual
        if dual_cover is not None:
            self.dual_cover = dual_cover
        self._relayout_keep_page()

    def rotate(self, delta: int):
        self.rotation = (self.rotation + delta) % 360
        self._cache.clear()
        self._cache_bytes = 0
        self._sel_cache = {}
        self._relayout_keep_page()

    def set_night(self, night: bool):
        self.night = night
        self.queue_draw()

    # == conversions ===========================================================================
    def page_to_doc(self, page: int, x: float, y: float) -> tuple[float, float]:
        w, h = self.document.sizes[page]
        rot = self.rotation
        if rot == 90:
            u, v = h - y, x
        elif rot == 180:
            u, v = w - x, h - y
        elif rot == 270:
            u, v = y, w - x
        else:
            u, v = x, y
        rx, ry, _rw, _rh = self._rects[page]
        s = self.scale
        return rx + u * s, ry + v * s

    def doc_to_page(self, page: int, dx: float, dy: float) -> tuple[float, float]:
        rx, ry, _rw, _rh = self._rects[page]
        s = self.scale
        u, v = (dx - rx) / s, (dy - ry) / s
        w, h = self.document.sizes[page]
        rot = self.rotation
        if rot == 90:
            return v, h - u
        if rot == 180:
            return w - u, h - v
        if rot == 270:
            return w - v, u
        return u, v

    def page_rect_to_view(self, page: int, rect) -> tuple[float, float, float, float]:
        x, y, w, h = rect
        x1, y1 = self.page_to_doc(page, x, y)
        x2, y2 = self.page_to_doc(page, x + w, y + h)
        hv, vv = self._hval(), self._vval()
        return (min(x1, x2) - hv, min(y1, y2) - vv, abs(x2 - x1), abs(y2 - y1))

    def hit(self, vx: float, vy: float, nearest: bool = False):
        """Page et point de page sous (vx, vy) de la vue ; nearest : la page la plus proche."""
        if self.document is None or not self._laid_out:
            return None
        dx, dy = vx + self._hval(), vy + self._vval()
        best = None
        for i, r in enumerate(self._rects):
            if r is None:
                continue
            x, y, w, h = r
            if x <= dx <= x + w and y <= dy <= y + h:
                px, py = self.doc_to_page(i, dx, dy)
                return i, px, py
            if nearest:
                ddx = 0 if x <= dx <= x + w else min(abs(dx - x), abs(dx - x - w))
                ddy = 0 if y <= dy <= y + h else min(abs(dy - y), abs(dy - y - h))
                d = ddx + ddy * 4
                if best is None or d < best[0]:
                    best = (d, i)
        if best is None:
            return None
        i = best[1]
        x, y, w, h = self._rects[i]
        dx = max(x, min(x + w, dx))
        dy = max(y, min(y + h, dy))
        px, py = self.doc_to_page(i, dx, dy)
        return i, px, py

    # == rendu =================================================================================
    def _visible_pages(self, extra: float = 0.0) -> list[int]:
        top = self._vval() - extra
        bottom = self._vval() + self.get_height() + extra
        out = []
        for i, r in enumerate(self._rects):
            if r is not None and r[1] + r[3] >= top and r[1] <= bottom:
                out.append(i)
        return out

    def _tiles(self, page: int, ds: float, visible_only: bool = True):
        """Tuiles de la page (en pixels de l'écran) : la page entière si elle est petite."""
        x, y, w, h = self._rects[page]
        pw, ph = max(1, int(math.ceil(w * ds))), max(1, int(math.ceil(h * ds)))
        skey = round(self.scale * ds, 4)
        if pw * ph <= WHOLE_PAGE_PX:
            return skey, [(0, 0, pw, ph)]
        hv, vv = self._hval(), self._vval()
        vx0, vy0 = (hv - x) * ds, (vv - y) * ds
        vx1, vy1 = vx0 + self.get_width() * ds, vy0 + self.get_height() * ds
        tiles = []
        for ty in range(0, ph, TILE):
            for tx in range(0, pw, TILE):
                tw, th = min(TILE, pw - tx), min(TILE, ph - ty)
                if visible_only and (tx + tw < vx0 or tx > vx1 or ty + th < vy0 or ty > vy1):
                    continue
                tiles.append((tx, ty, tw, th))
        return skey, tiles

    def _preview_scale(self, page: int) -> float:
        w, h = self.document.size(page, self.rotation)
        return math.sqrt(PREVIEW_PX / (w * h))

    def _schedule_requests(self):
        if self._requests_pending:
            return
        self._requests_pending = True
        GLib.idle_add(self._send_requests)

    def _send_requests(self):
        self._requests_pending = False
        if self.renderer is None or not self._laid_out or self.get_width() <= 0:
            return False
        ds = self._device_scale()
        visible = self._visible_pages()
        near = self._visible_pages(self.get_height())
        view_reqs, prev_reqs = [], []
        center = self._vval() + self.get_height() / 2
        for i in sorted(visible, key=lambda i: abs(self._rects[i][1] + self._rects[i][3] / 2 - center)):
            if not self._settled:
                break
            skey, tiles = self._tiles(i, ds)
            for t in tiles:
                key = ("v", i, skey, self.rotation, t)
                if key not in self._cache:
                    view_reqs.append((key, i, skey, self.rotation, t))
        if self._settled:
            for i in near:
                if i in visible:
                    continue
                skey, tiles = self._tiles(i, ds, visible_only=False)
                if len(tiles) == 1:
                    key = ("v", i, skey, self.rotation, tiles[0])
                    if key not in self._cache:
                        view_reqs.append((key, i, skey, self.rotation, tiles[0]))
        for i in near:
            key = ("p", i, self.rotation)
            if key not in self._previews:
                sc = self._preview_scale(i)
                w, h = self.document.size(i, self.rotation)
                prev_reqs.append((key, i, sc, self.rotation, (0, 0, max(1, int(w * sc)), max(1, int(h * sc)))))
        self.renderer.want("view", view_reqs)
        self.renderer.want("preview", prev_reqs)
        return False

    def request_thumbs(self, requests: list):
        if self.renderer is not None:
            self.renderer.want("thumbs", requests)

    def _deliver(self, key, texture):
        if key[0] == "t":
            if self.thumb_listener is not None:
                self.thumb_listener(key, texture)
            return
        if texture is None:
            return
        if key[0] == "p":
            self._stale_previews.pop(key[1], None)
            self._previews[key] = texture
            self._previews.move_to_end(key)
            while len(self._previews) > PREVIEWS:
                self._previews.popitem(last=False)
        else:
            self._stale_tiles.pop(key, None)
            size = texture.get_width() * texture.get_height() * 4
            self._cache[key] = texture
            self._cache_bytes += size
            self._evict()
        if self._laid_out and key[1] in self._visible_pages(self.get_height()):
            self.queue_draw()
        if self._link_popover is not None:
            self._link_popover.get_child().queue_draw()

    def _evict(self):
        if self._cache_bytes <= CACHE_BYTES:
            return
        keep = set(self._visible_pages())
        for key in list(self._cache):
            if self._cache_bytes <= CACHE_BYTES * 0.8:
                break
            if key[1] in keep and key[2] == round(self.scale * self._device_scale(), 4):
                continue
            tex = self._cache.pop(key)
            self._cache_bytes -= tex.get_width() * tex.get_height() * 4

    def preview_texture(self, page: int):
        """Aperçu de la page (pour la bulle des liens) : demandé s'il manque."""
        key = ("p", page, self.rotation)
        tex = self._previews.get(key)
        if tex is None and self.renderer is not None:
            sc = self._preview_scale(page)
            w, h = self.document.size(page, self.rotation)
            self.renderer.want("preview", [(key, page, sc, self.rotation,
                                            (0, 0, max(1, int(w * sc)), max(1, int(h * sc))))])
        return tex

    def do_snapshot(self, snap):
        if self.document is None or not self._laid_out:
            return
        ds = self._device_scale()
        hv, vv = self._hval(), self._vval()
        sel_color = self._accent(0.35)
        for i in self._visible_pages():
            x, y, w, h = self._rects[i]
            x, y = x - hv, y - vv
            rect = _rect(x, y, w, h)
            shadow = Gsk.RoundedRect()
            shadow.init_from_rect(rect, 0)
            snap.append_outset_shadow(shadow, SHADOW, 0, 1, 0, 4)
            if self.night:
                snap.push_color_matrix(*INVERT)
            snap.append_color(WHITE, rect)
            prev = self._previews.get(("p", i, self.rotation)) or self._stale_previews.get(i)
            if prev is not None:
                snap.append_texture(prev, rect)
            skey, tiles = self._tiles(i, ds)
            for t in tiles:
                key = ("v", i, skey, self.rotation, t)
                tex = self._cache.get(key) or self._stale_tiles.get(key)
                if tex is None:
                    continue
                tx, ty, tw, th = t
                snap.append_texture(tex, _rect(x + tx / ds, y + ty / ds, tw / ds, th / ds))
            if self.night:
                snap.pop()
            for k, r in enumerate(self.search_results.get(i, ())):
                current = self.search_current == (i, k)
                snap.append_color(FOUND_CURRENT if current else FOUND, _rect(*self.page_rect_to_view(i, r)))
            if self._sel is not None:
                for r in self._selection_rects(i):
                    snap.append_color(sel_color, _rect(*self.page_rect_to_view(i, r)))
            if self.show_fields and self.annotator is not None:
                fields = self.document.fields(i)
                if fields:
                    tint = self._accent(0.10)
                    for f in fields:
                        if not f.read_only and f.kind != "signature":
                            snap.append_color(tint, _rect(*self.page_rect_to_view(i, f.rect)))
            sa = self.selected_annot
            if sa is not None and sa.page == i:
                self._outline(snap, self.page_rect_to_view(i, sa.rect), self._accent(1.0))
            for r in self.redactions.get(i, ()):
                vr = self.page_rect_to_view(i, r)
                snap.append_color(REDACT, _rect(*vr))
                self._outline(snap, vr, self._accent(1.0))
            if self._band is not None and self._band[0] == i:
                _p, x1, y1, x2, y2 = self._band
                r = self.page_rect_to_view(i, (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)))
                self._outline(snap, r, self._accent(1.0))
                snap.append_color(self._accent(0.12), _rect(*r))
        if self._editor is not None:
            self.snapshot_child(self._editor[0], snap)

    def _outline(self, snap, r, color):
        x, y, w, h = r
        rr = Gsk.RoundedRect()
        rr.init_from_rect(_rect(x - 2, y - 2, w + 4, h + 4), 2)
        snap.append_border(rr, [2, 2, 2, 2], [color] * 4)

    def _accent(self, alpha: float) -> Gdk.RGBA:
        c = Gdk.RGBA()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ok, found = self.get_style_context().lookup_color("accent_bg_color")
        if ok:
            c = found
        else:
            c.parse("#3584e4")
        c.alpha = alpha
        return c

    # == sélection =============================================================================
    def has_selection(self) -> bool:
        return self._sel is not None

    def clear_selection(self):
        if self._sel is not None:
            self._sel = None
            self._sel_cache = {}
            self.queue_draw()
            self.emit("selection-changed")

    def _set_selection(self, start, end, style: str):
        if (start[0], start[2], start[1]) > (end[0], end[2], end[1]) and start[0] != end[0]:
            start, end = end, start
        elif start[0] > end[0]:
            start, end = end, start
        self._sel = (start, end, style)
        self._sel_cache = {}
        self.queue_draw()
        self.emit("selection-changed")

    def select_all(self):
        if self.document is None:
            return
        n = self.document.n_pages - 1
        w, h = self.document.sizes[n]
        self._set_selection((0, 0.0, 0.0), (n, w, h), "glyph")

    def _selection_span(self, page: int):
        start, end, style = self._sel
        if not (start[0] <= page <= end[0]):
            return None
        w, h = self.document.sizes[page]
        x1, y1 = (start[1], start[2]) if page == start[0] else (0.0, 0.0)
        x2, y2 = (end[1], end[2]) if page == end[0] else (w, h)
        return x1, y1, x2, y2, style

    def _selection_rects(self, page: int) -> list:
        cached = self._sel_cache.get(page)
        if cached is None:
            span = self._selection_span(page)
            cached = ([], "") if span is None else self.document.selection(page, *span)
            self._sel_cache[page] = cached
        return cached[0]

    def selected_text(self) -> str:
        if self._sel is None:
            return ""
        parts = []
        for page in range(self._sel[0][0], self._sel[1][0] + 1):
            if page not in self._sel_cache:
                span = self._selection_span(page)
                self._sel_cache[page] = ([], "") if span is None else self.document.selection(page, *span)
            text = self._sel_cache[page][1]
            if text:
                parts.append(text)
        return "\n".join(parts)

    def copy_selection(self) -> bool:
        text = self.selected_text()
        if text:
            self.get_clipboard().set(text)
            return True
        return False

    # == souris ================================================================================
    def _on_pressed(self, gesture, n_press, x, y):
        self.grab_focus()
        button = gesture.get_current_button()
        hit = self.hit(x, y)
        if button == 3:
            info = {"hit": hit, "link": None, "image": None, "annot": None}
            if hit is not None:
                info["link"] = self.document.link_at(*hit)
                info["image"] = self.document.image_at(*hit)
                info["annot"] = self.document.annot_at(*hit)
            self.emit("context-menu", x, y, info)
            return
        if button != 1:
            return
        self._hide_link_preview()
        self.close_editor(commit=True)
        self._press = (x, y, hit)
        self._dragging = False
        self._band = None
        if hit is None:
            self.clear_selection()
            self.select_annot(None)
            return
        if self.tool in ("square", "circle", "note", "redact", "sign"):
            return
        if n_press == 2:
            self._set_selection(hit, hit, "word")
            self._press = (x, y, hit, "word")
        elif n_press >= 3:
            self._set_selection(hit, hit, "line")
            self._press = (x, y, hit, "line")

    def _on_released(self, gesture, n_press, x, y):
        if gesture.get_current_button() != 1 or self._press is None:
            return
        press = self._press
        self._press = None
        if self._dragging:
            return
        if n_press > 1:
            if self.tool in MARKUP_TOOLS and self._sel is not None and self.annotator is not None:
                self.annotator.markup(self.tool)
            return
        hit = press[2]
        if hit is None or self.document is None:
            return
        if self.tool == "note" and self.annotator is not None:
            self.annotator.add_note(*hit)
            return
        if self.tool == "redact":
            self._remove_redaction(hit)
            return
        if self.tool in ("square", "circle"):
            return
        doc = self.document
        if self.annotator is not None and self.tool == "select":
            field = doc.field_at(*hit)
            if field is not None and not field.read_only:
                self.clear_selection()
                self.annotator.activate_field(field)
                return
            info = doc.annot_at(*hit)
            if info is not None:
                self.clear_selection()
                self.select_annot(info)
                self.annotator.edit(info)
                return
        link = doc.link_at(*hit)
        self.select_annot(None)
        if link is not None:
            self.follow(link)
        else:
            self.clear_selection()

    def _on_drag_update(self, gesture, ox, oy):
        if self._press is None:
            return
        ok, sx, sy = gesture.get_start_point()
        if not self._dragging and abs(ox) + abs(oy) < 4:
            return
        x, y = sx + ox, sy + oy
        start = self._press[2] if self._press[2] is not None else self.hit(sx, sy, nearest=True)
        if start is None:
            return
        self._dragging = True
        if self.tool in ("square", "circle", "redact", "sign"):
            page = start[0]
            end = self._hit_in_page(page, x, y)
            self._band = (page, start[1], start[2], end[0], end[1])
            self.queue_draw()
        elif self.tool != "note":
            end = self.hit(x, y, nearest=True)
            if end is not None:
                style = self._press[3] if len(self._press) > 3 else "glyph"
                self._set_selection(start, end, style)
        # défilement automatique près des bords
        h = self.get_height()
        dy = (y - (h - 24)) if y > h - 24 else (y - 24 if y < 24 else 0)
        self._autoscroll_dy = dy
        self._drag_pos = (gesture, sx, sy, ox, oy)
        if dy and not self._autoscroll:
            self._autoscroll = GLib.timeout_add(30, self._autoscroll_tick)

    def _hit_in_page(self, page: int, vx: float, vy: float) -> tuple[float, float]:
        """Point de la vue ramené dans la page (rectangle tracé jusqu'au bord)."""
        x, y, w, h = self._rects[page]
        dx = max(x, min(x + w, vx + self._hval()))
        dy = max(y, min(y + h, vy + self._vval()))
        return self.doc_to_page(page, dx, dy)

    def _on_drag_end(self, *_):
        self._autoscroll_dy = 0
        if not self._dragging or self._press is None:
            return
        self._press = None
        if self.annotator is None:
            return
        if self.tool in ("square", "circle", "redact", "sign") and self._band is not None:
            page, x1, y1, x2, y2 = self._band
            self._band = None
            self.queue_draw()
            rect = (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
            if rect[2] >= 3 and rect[3] >= 3:
                if self.tool == "redact":
                    self.redactions.setdefault(page, []).append(rect)
                    self.emit("redactions-changed")
                elif self.tool == "sign":
                    self.emit("area-drawn", page, rect)
                else:
                    self.annotator.add_shape(page, self.tool, rect)
        elif self.tool in MARKUP_TOOLS and self._sel is not None:
            self.annotator.markup(self.tool)

    def _autoscroll_tick(self):
        if not self._autoscroll_dy or self._press is None:
            self._autoscroll = 0
            return False
        self._set_values(None, self._vval() + self._autoscroll_dy * 0.6)
        gesture, sx, sy, ox, oy = self._drag_pos
        self._on_drag_update(gesture, ox, oy)
        return True

    def _on_pan_begin(self, *_):
        self._pan = (self._hval(), self._vval())

    def _on_pan_update(self, _g, ox, oy):
        hv, vv = self._pan
        self._set_values(hv - ox, vv - oy)

    def _on_motion(self, _c, x, y):
        self._pointer_pos = (x, y)
        hit = self.hit(x, y)
        cursor = "default"
        link = None
        if hit is not None and self.document is not None:
            doc = self.document
            field = doc.field_at(*hit) if self.annotator is not None and self.tool == "select" else None
            link = doc.link_at(*hit) if field is None else None
            if self.tool in ("square", "circle", "note", "redact", "sign"):
                cursor = "crosshair"
            elif field is not None and not field.read_only:
                cursor = "text" if field.kind in ("text", "multiline", "password") else "pointer"
            elif self.tool == "select" and doc.annot_at(*hit) is not None and not doc.char_at(*hit):
                cursor = "pointer"
            elif link is not None and self.tool == "select":
                cursor = "pointer"
            elif doc.char_at(*hit):
                cursor = "text"
        self.set_cursor_from_name(cursor)
        if link is not self._hover_link:
            self._hover_link = link
            self._hide_link_preview()
            if self._preview_timer:
                GLib.source_remove(self._preview_timer)
                self._preview_timer = 0
            if link is not None and link.kind == "page" and features.enabled("link_preview"):
                self._preview_timer = GLib.timeout_add(450, self._show_link_preview, link, x, y)

    def _end_hover(self):
        self._hover_link = None
        if self._preview_timer:
            GLib.source_remove(self._preview_timer)
            self._preview_timer = 0
        self._hide_link_preview()

    def _show_link_preview(self, link, x, y):
        self._preview_timer = 0
        if self._press is not None or self.document is None:
            return False
        # une seule bulle, gardée : en détruire une juste fermée faisait planter GTK 4.14
        pop = self._link_pop
        if pop is None:
            pop = self._link_pop = Gtk.Popover(autohide=False, has_arrow=True, can_focus=False)
            pop.add_css_class("calypso-link-preview")
            pop.set_parent(self)
        pop.set_child(LinkPreview(self, link))
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
        pop.set_pointing_to(r)
        self._link_popover = pop
        pop.popup()
        return False

    def _hide_link_preview(self):
        pop, self._link_popover = self._link_popover, None
        if pop is not None:
            pop.popdown()

    def _on_scroll(self, controller, dx, dy):
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            pos = self._pointer_pos
            if dy < 0:
                self.set_zoom(self.zoom * (1.1 if abs(dy) < 1 else 1.15), pos)
            elif dy > 0:
                self.set_zoom(self.zoom / (1.1 if abs(dy) < 1 else 1.15), pos)
            return True
        if not self.continuous and dy:
            va = self.props.vadjustment
            at_end = va is None or va.get_value() >= va.get_upper() - va.get_page_size() - 0.5
            at_start = va is None or va.get_value() <= 0.5
            if (dy > 0 and at_end) or (dy < 0 and at_start):
                self._nonc_scroll += dy
                if abs(self._nonc_scroll) >= 1:
                    self._nonc_scroll = 0
                    if dy > 0:
                        self.next_page()
                    else:
                        self.previous_page()
                        GLib.idle_add(lambda: (self._set_values(None, 1e9), False)[1])
                return True
        return False

    def _on_pinch(self, _g, scale):
        self.set_zoom(self._pinch_zoom * scale)

    # == clavier ===============================================================================
    def _on_key(self, _c, keyval, _code, state):
        if self.document is None:
            return False
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        if alt:
            return False
        k = Gdk.keyval_name(keyval) or ""
        ha = self.props.hadjustment
        if k in ("Down", "KP_Down") and not ctrl:
            self._step(40)
        elif k in ("Up", "KP_Up") and not ctrl:
            self._step(-40)
        elif k in ("Page_Down", "KP_Page_Down") or (k == "space" and not shift and not ctrl):
            self._page_step(1)
        elif k in ("Page_Up", "KP_Page_Up") or (k == "space" and shift) or (k == "BackSpace" and not ctrl):
            self._page_step(-1)
        elif k in ("Right", "Left", "KP_Right", "KP_Left") and not ctrl:
            forward = "Right" in k
            if ha is not None and ha.get_upper() - ha.get_page_size() > 1:
                ha.set_value(ha.get_value() + (40 if forward else -40))
            elif forward:
                self.next_page()
            else:
                self.previous_page()
        elif k in ("Home", "KP_Home"):
            self.first_page()
        elif k in ("End", "KP_End"):
            self.last_page()
        elif k == "n" and not ctrl:
            self.next_page()
        elif k == "p" and not ctrl:
            self.previous_page()
        elif k in ("plus", "equal", "KP_Add") and not ctrl:
            self.zoom_in()
        elif k in ("minus", "KP_Subtract") and not ctrl:
            self.zoom_out()
        elif k == "w" and not ctrl:
            self.set_zoom_mode("fit-width")
        elif k == "f" and not ctrl:
            self.set_zoom_mode("fit-page")
        elif k == "Escape" and self._sel is not None:
            self.clear_selection()
        elif k == "Escape" and self.selected_annot is not None:
            self.select_annot(None)
        elif k in ("Delete", "KP_Delete") and self.selected_annot is not None and self.annotator is not None:
            self.annotator.delete(self.selected_annot)
        else:
            return False
        return True

    def _step(self, dy: float):
        va = self.props.vadjustment
        if va is None:
            return
        if not self.continuous:
            if dy > 0 and va.get_value() >= va.get_upper() - va.get_page_size() - 0.5:
                self.next_page()
                return
            if dy < 0 and va.get_value() <= 0.5:
                self.previous_page()
                return
        va.set_value(va.get_value() + dy)

    def _page_step(self, direction: int):
        va = self.props.vadjustment
        if va is None:
            return
        if not self.continuous:
            at_end = va.get_value() >= va.get_upper() - va.get_page_size() - 0.5
            at_start = va.get_value() <= 0.5
            if direction > 0 and at_end:
                self.next_page()
                return
            if direction < 0 and at_start:
                self.previous_page()
                return
        va.set_value(va.get_value() + direction * va.get_page_size() * 0.9)

    # == annotations et formulaires ===========================================================
    def set_tool(self, tool: str):
        self.tool = tool
        self._band = None
        if tool != "select":
            self.select_annot(None)
        if tool in MARKUP_TOOLS and self._sel is not None and self.annotator is not None:
            self.annotator.markup(tool)             # texte déjà sélectionné : annoté tout de suite
        self.queue_draw()

    def _remove_redaction(self, hit):
        """Mode caviardage : un clic dans une zone la retire."""
        page, x, y = hit
        rects = self.redactions.get(page, [])
        for r in reversed(rects):
            rx, ry, rw, rh = r
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                rects.remove(r)
                self.queue_draw()
                self.emit("redactions-changed")
                return

    def clear_redactions(self):
        self.redactions = {}
        self.queue_draw()
        self.emit("redactions-changed")

    def select_annot(self, info):
        if info is self.selected_annot:
            return
        self.selected_annot = info
        self.queue_draw()

    def invalidate_page(self, page: int):
        """La page a changé (annotation, champ) : rendus et aperçu oubliés, redessinés."""
        # les anciennes tuiles restent affichées jusqu'aux nouvelles : pas de passage par le flou
        for key in [k for k in self._cache if k[1] == page]:
            tex = self._cache.pop(key)
            self._cache_bytes -= tex.get_width() * tex.get_height() * 4
            self._stale_tiles[key] = tex
        # l'aperçu reste affiché (étiré) jusqu'au nouveau rendu : pas de clignotement blanc
        stale = self._previews.get(("p", page, self.rotation))
        self._previews.pop(("p", page, self.rotation), None)
        if stale is not None:
            self._stale_previews[page] = stale
        if self.selected_annot is not None and self.selected_annot.page == page:
            fresh = self.document.find_annot(self.selected_annot.annot) if self.document else None
            self.selected_annot = fresh
        self._schedule_requests()
        self.queue_draw()
        self.emit("page-invalidated", page)

    def open_editor(self, widget: Gtk.Widget, page: int, rect):
        """Champ de saisie posé sur un champ de formulaire de la page."""
        self.close_editor(commit=True)
        self._editor = (widget, page, rect)
        widget.set_parent(self)
        # placé dans NOTRE size_allocate : un size_allocate du champ fait à part n'était pas dessiné
        self.queue_allocate()
        widget.grab_focus()

    def close_editor(self, commit: bool = True):
        ed, self._editor = self._editor, None
        if ed is None:
            return
        widget = ed[0]
        done = getattr(widget, "_calypso_done", None)
        if done is not None:
            widget._calypso_done = None
            done(commit)
        widget.unparent()
        self.queue_draw()

    def _place_editor(self):
        if self._editor is None or not self._laid_out:
            return
        widget, page, rect = self._editor
        if not self._shows(page):
            return
        x, y, w, h = self.page_rect_to_view(page, rect)
        widget.measure(Gtk.Orientation.HORIZONTAL, -1)
        alloc = Gdk.Rectangle()
        alloc.x, alloc.y = int(x), int(y)
        alloc.width, alloc.height = max(24, int(w)), max(int(h), widget.measure(Gtk.Orientation.VERTICAL,
                                                                                  int(w))[0])
        widget.size_allocate(alloc, -1)

    def editor_widget(self):
        return self._editor[0] if self._editor is not None else None

    def _on_tooltip(self, _w, x, y, keyboard, tooltip):
        if keyboard or self.document is None:
            return False
        hit = self.hit(x, y)
        if hit is None:
            return False
        info = self.document.annot_at(*hit)
        if info is None or not (info.contents or info.author):
            return False
        text = info.contents.strip()
        if info.author:
            text = (text + "\n" if text else "") + "— " + info.author
        tooltip.set_text(text[:600])
        return True

    # == recherche =============================================================================
    def set_search_results(self, results: dict, current=None):
        self.search_results = results
        self.search_current = current
        self.queue_draw()

    def show_result(self, page: int, index: int):
        self.search_current = (page, index)
        rects = self.search_results.get(page) or []
        if not (0 <= index < len(rects)):
            self.queue_draw()
            return
        if not self.continuous and not self._shows(page):
            self.go_to(page, rects[index][1])
            self.queue_draw()
            return
        if not self._laid_out:
            return
        vx, vy, vw, vh = self.page_rect_to_view(page, rects[index])
        H, W = self.get_height(), self.get_width()
        if vy < 0 or vy + vh > H:
            self._set_values(None, self._vval() + vy - H / 3)
        if vx < 0 or vx + vw > W:
            self._set_values(self._hval() + vx - W / 3, None)
        if page != self.page and self.continuous:
            self._update_page()
        self.queue_draw()


class LinkPreview(Gtk.Widget):
    """Contenu de la bulle d'aperçu d'un lien : la page visée, autour du point visé."""
    __gtype_name__ = "CalypsoLinkPreview"
    WIDTH, HEIGHT = 340, 220

    def __init__(self, view: DocView, link: Link):
        super().__init__()
        self.view = view
        self.link = link
        self.set_size_request(self.WIDTH, self.HEIGHT)
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def do_snapshot(self, snap):
        view = self.view
        if view.document is None:
            return
        page = self.link.page
        tex = view.preview_texture(page)
        w, h = view.document.size(page, view.rotation)
        scale = self.WIDTH / w
        top = 0.0
        if self.link.top is not None and view.rotation == 0:
            top = self.link.top * scale - 12
        top = max(0.0, min(top, h * scale - self.HEIGHT))
        rect = _rect(0, -top, self.WIDTH, h * scale)
        if view.night:
            snap.push_color_matrix(*INVERT)
        snap.append_color(WHITE, rect)
        if tex is not None:
            snap.append_texture(tex, rect)
        if view.night:
            snap.pop()
