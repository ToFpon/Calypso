"""Panneau latéral comme Papers : miniatures des pages, sommaire, résultats de la recherche."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from ..i18n import tr  # noqa: E402
from .common import clear_box, shown  # noqa: E402

THUMB_W = 132


class Thumb(Gtk.Widget):
    """Miniature d'une page : fond blanc et ombre comme dans la vue, texture posée dessus."""
    __gtype_name__ = "CalypsoThumb"

    def __init__(self, sidebar):
        super().__init__()
        self.sidebar = sidebar
        self.page = -1
        self.set_halign(Gtk.Align.CENTER)

    def set_page(self, page: int):
        self.page = page
        self.queue_resize()
        self.queue_draw()

    def _size(self):
        view = self.sidebar.view
        if view.document is None or self.page < 0:
            return THUMB_W, int(THUMB_W * 1.414)
        w, h = view.document.size(self.page, view.rotation)
        if h > w * 3:                          # page très haute : bornée
            return max(24, int(THUMB_W * 3 * w / h)), THUMB_W * 3
        return THUMB_W, max(24, int(THUMB_W * h / w))

    def do_measure(self, orientation, for_size):
        w, h = self._size()
        n = w if orientation == Gtk.Orientation.HORIZONTAL else h
        return (n, n, -1, -1)

    def do_snapshot(self, snap):
        from .view import INVERT, SHADOW, WHITE, _rect
        from gi.repository import Gsk
        w, h = self.get_width(), self.get_height()
        rect = _rect(0, 0, w, h)
        rr = Gsk.RoundedRect()
        rr.init_from_rect(rect, 0)
        snap.append_outset_shadow(rr, SHADOW, 0, 1, 0, 3)
        night = self.sidebar.view.night
        if night:
            snap.push_color_matrix(*INVERT)
        snap.append_color(WHITE, rect)
        tex = self.sidebar.thumb(self.page)
        if tex is not None:
            snap.append_texture(tex, rect)
        if night:
            snap.pop()


class OutlineObj(GObject.Object):
    def __init__(self, item):
        super().__init__()
        self.item = item


class Sidebar(Gtk.Box):
    def __init__(self, view):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("calypso-sidebar")
        self.view = view
        self._thumbs: dict = {}
        self._thumb_dirty: set = set()
        self._bound: dict = {}               # cellule → page
        self._thumb_req = False
        self._syncing = False
        view.thumb_listener = self._on_thumb
        view.connect("page-changed", lambda _v, p: self.select_page(p))

        self.switcher = Gtk.Box(spacing=0, halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=6,
                                css_classes=["linked"])
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.append(self.switcher)
        self.append(self.stack)
        self._buttons: dict = {}
        self._add_page("thumbnails", "view-grid-symbolic", tr("Miniatures"), self._build_thumbs())
        self._add_page("outline", "view-list-bullet-symbolic", tr("Sommaire"), self._build_outline())
        self._add_page("annots", "calypso-note-symbolic", tr("Annotations"), self._build_annots())
        self._add_page("signatures", "security-high-symbolic", tr("Signatures"), self._build_signatures())
        self._buttons["signatures"].set_visible(False)
        self._add_page("results", "edit-find-symbolic", tr("Résultats de la recherche"), self._build_results())
        self._buttons["results"].set_visible(False)
        self._annots_stale = True
        self.stack.connect("notify::visible-child-name", lambda *_: self._maybe_refresh_annots())
        view.connect("page-invalidated", self._on_page_invalidated)

    # -- pages ------------------------------------------------------------------------------------
    def _add_page(self, name, icon, title, child):
        self.stack.add_titled(child, name, title)
        b = Gtk.ToggleButton(icon_name=icon, tooltip_text=title)
        first = next(iter(self._buttons.values()), None)
        if first is not None:
            b.set_group(first)
        b.connect("toggled", lambda btn, n=name: btn.get_active() and self.show_page(n))
        self.switcher.append(b)
        self._buttons[name] = b

    def show_page(self, name: str):
        if not self._buttons[name].get_visible():
            name = "thumbnails"
        self.stack.set_visible_child_name(name)
        if not self._buttons[name].get_active():
            self._buttons[name].set_active(True)

    def current_page(self) -> str:
        return self.stack.get_visible_child_name() or "thumbnails"

    # -- document -------------------------------------------------------------------------------
    def set_document(self, document):
        self._thumbs.clear()
        self._thumb_dirty.clear()
        n = document.n_pages if document is not None else 0
        self.thumb_model.splice(0, self.thumb_model.get_n_items(), [str(i) for i in range(n)])
        outline = document.outline() if document is not None else []
        self.outline_root.remove_all()
        for item in outline:
            self.outline_root.append(OutlineObj(item))
        has = bool(outline)
        self._buttons["outline"].set_visible(has)
        if not has and self.current_page() == "outline":
            self.show_page("thumbnails")
        self.set_results(None)
        self._annots_stale = True
        self._maybe_refresh_annots()
        if document is not None:
            self.select_page(self.view.page)

    def rotation_changed(self):
        self._thumbs.clear()
        for cell, page in list(self._bound.items()):
            cell._thumb.set_page(page)
        self._request_thumbs()

    # -- miniatures -----------------------------------------------------------------------------
    def _build_thumbs(self):
        self.thumb_model = Gtk.StringList()
        self.thumb_sel = Gtk.SingleSelection(model=self.thumb_model, autoselect=False, can_unselect=False)
        self.thumb_sel.connect("notify::selected", self._on_thumb_selected)
        f = Gtk.SignalListItemFactory()
        f.connect("setup", self._thumb_setup)
        f.connect("bind", self._thumb_bind)
        f.connect("unbind", self._thumb_unbind)
        self.thumb_list = Gtk.ListView(model=self.thumb_sel, factory=f, single_click_activate=False)
        self.thumb_list.add_css_class("navigation-sidebar")
        self.thumb_list.add_css_class("calypso-thumbs")
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.thumb_list)
        return sw

    def _thumb_setup(self, _f, li):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=4)
        box._thumb = Thumb(self)
        box._label = Gtk.Label(css_classes=["caption"])
        box.append(box._thumb)
        box.append(box._label)
        li.set_child(box)

    def _thumb_bind(self, _f, li):
        box = li.get_child()
        page = int(li.get_item().get_string())
        box._thumb.set_page(page)
        doc = self.view.document
        box._label.set_label(shown(doc.page_label(page)) if doc is not None else "")
        self._bound[box] = page
        self._request_thumbs()

    def _thumb_unbind(self, _f, li):
        self._bound.pop(li.get_child(), None)

    def thumb(self, page: int):
        return self._thumbs.get((page, self.view.rotation))

    def _request_thumbs(self):
        if self._thumb_req:
            return
        self._thumb_req = True

        def send():
            self._thumb_req = False
            doc = self.view.document
            if doc is None:
                return False
            ds = self.view._device_scale()
            reqs = []
            rot = self.view.rotation
            for page in sorted(set(self._bound.values())):
                if ((page, rot) in self._thumbs and page not in self._thumb_dirty) or page >= doc.n_pages:
                    continue
                w, h = doc.size(page, rot)
                tw = THUMB_W if h <= w * 3 else THUMB_W * 3 * w / h
                sc = tw * ds / w
                reqs.append((("t", page, rot), page, sc, rot, (0, 0, max(1, int(w * sc)), max(1, int(h * sc)))))
            self.view.request_thumbs(reqs)
            return False
        GLib.idle_add(send)

    def _on_thumb(self, key, texture):
        _t, page, rot = key
        if texture is None or rot != self.view.rotation:
            return
        self._thumbs[(page, rot)] = texture
        self._thumb_dirty.discard(page)
        for cell, p in self._bound.items():
            if p == page:
                cell._thumb.queue_draw()

    def select_page(self, page: int):
        if self.thumb_model.get_n_items() <= page:
            return
        self._syncing = True
        self.thumb_sel.set_selected(page)
        self._syncing = False
        if self.get_mapped() and self.current_page() == "thumbnails":
            self.thumb_list.scroll_to(page, Gtk.ListScrollFlags.NONE, None)

    def _on_thumb_selected(self, sel, _p):
        if self._syncing:
            return
        page = sel.get_selected()
        if page != Gtk.INVALID_LIST_POSITION and page != self.view.page:
            self.view.go_to(page, remember=True)

    # -- sommaire ---------------------------------------------------------------------------------
    def _build_outline(self):
        self.outline_root = Gio.ListStore.new(OutlineObj)

        def children(obj):
            if not obj.item.children:
                return None
            store = Gio.ListStore.new(OutlineObj)
            for c in obj.item.children:
                store.append(OutlineObj(c))
            return store
        self.outline_tree = Gtk.TreeListModel.new(self.outline_root, False, False, children)
        sel = Gtk.NoSelection(model=self.outline_tree)
        f = Gtk.SignalListItemFactory()
        f.connect("setup", self._outline_setup)
        f.connect("bind", self._outline_bind)
        lv = Gtk.ListView(model=sel, factory=f, single_click_activate=True)
        lv.add_css_class("navigation-sidebar")
        lv.connect("activate", self._outline_activate)
        self.outline_list = lv
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=lv)

    def _outline_setup(self, _f, li):
        exp = Gtk.TreeExpander()
        box = Gtk.Box(spacing=6)
        exp._title = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        exp._page = Gtk.Label(css_classes=["dim-label", "numeric"])
        box.append(exp._title)
        box.append(exp._page)
        exp.set_child(box)
        li.set_child(exp)

    def _outline_bind(self, _f, li):
        exp = li.get_child()
        row = li.get_item()
        exp.set_list_row(row)
        obj = row.get_item()
        title = shown(obj.item.title) or "—"
        exp._title.set_label(title)
        exp.set_tooltip_text(title)
        link = obj.item.link
        doc = self.view.document
        exp._page.set_label(shown(doc.page_label(link.page)) if link.kind == "page" and doc is not None else "")
        if obj.item.expanded and not getattr(obj, "_opened", False):
            obj._opened = True
            GLib.idle_add(lambda: (row.set_expanded(True), False)[1])

    def _outline_activate(self, _lv, pos):
        row = self.outline_tree.get_item(pos)
        if row is not None:
            self.view.follow(row.get_item().item.link)

    def _on_page_invalidated(self, _v, page):
        self._thumb_dirty.add(page)          # l'ancienne miniature reste jusqu'à la nouvelle
        self._request_thumbs()
        self._annots_stale = True
        self._maybe_refresh_annots()

    # -- annotations ------------------------------------------------------------------------------
    def _build_annots(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.annots_empty = Gtk.Label(label=tr("Aucune annotation"), css_classes=["dim-label"], margin_top=18,
                                      wrap=True)
        self.annots_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.annots_list.connect("row-activated", self._annot_activated)
        box.append(self.annots_empty)
        box.append(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=self.annots_list))
        return box

    def _maybe_refresh_annots(self):
        if not self._annots_stale or self.current_page() != "annots":
            return
        self._annots_stale = False
        from .annotations import kind_icon, kind_label
        clear_box(self.annots_list)
        doc = self.view.document
        count = 0
        if doc is not None:
            for page in range(doc.n_pages):
                for info in doc.annots(page):
                    row = Gtk.ListBoxRow()
                    row._info = info
                    line = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4)
                    line.append(Gtk.Image(icon_name=kind_icon(info.kind), valign=Gtk.Align.START))
                    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
                    first = info.contents.strip().splitlines()[0] if info.contents.strip() else kind_label(info.kind)
                    texts.append(Gtk.Label(label=shown(first), xalign=0, ellipsize=Pango.EllipsizeMode.END))
                    sub = tr("Page {0}").format(shown(doc.page_label(page)))
                    if info.author:
                        sub += " · " + shown(info.author)
                    texts.append(Gtk.Label(label=sub, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                           css_classes=["dim-label", "caption"]))
                    line.append(texts)
                    row.set_child(line)
                    self.annots_list.append(row)
                    count += 1
        self.annots_empty.set_visible(count == 0)

    def _annot_activated(self, _lb, row):
        info = row._info
        fresh = self.view.document.find_annot(info.annot) if self.view.document else None
        if fresh is None:
            return
        self.view.go_to(fresh.page, max(0.0, fresh.rect[1] - 40), remember=True)
        self.view.select_annot(fresh)

    # -- signatures --------------------------------------------------------------------------------
    def _build_signatures(self):
        self.sig_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.sig_list.connect("row-activated", self._sig_activated)
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=self.sig_list)

    def set_signatures(self, sigs: list):
        from .signatures import signature_row
        clear_box(self.sig_list)
        for sig in sigs:
            self.sig_list.append(signature_row(sig))
        self._buttons["signatures"].set_visible(bool(sigs))
        if not sigs and self.current_page() == "signatures":
            self.show_page("thumbnails")

    def _sig_activated(self, _lb, row):
        sig = row._sig
        if sig.page >= 0:
            top = max(0.0, sig.rect[1] - 40) if sig.rect else None
            self.view.go_to(sig.page, top, remember=True)

    # -- résultats de la recherche ----------------------------------------------------------------
    def _build_results(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.results_label = Gtk.Label(css_classes=["dim-label"], margin_top=6, margin_bottom=6, wrap=True)
        self.results_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.results_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.results_list.connect("row-activated", self._result_activated)
        self.results_list.set_sort_func(lambda a, b: (a._target > b._target) - (a._target < b._target))
        box.append(self.results_label)
        box.append(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      child=self.results_list))
        self._result_rows: list = []
        return box

    def set_results(self, search):
        """search : l'objet Search de la fenêtre (None : recherche fermée)."""
        clear_box(self.results_list)
        self._result_rows = []
        self.search = search
        visible = search is not None and bool(search.query)
        self._buttons["results"].set_visible(visible)
        if visible:
            # comme Papers : la liste des résultats s'affiche ; on reviendra au panneau d'avant
            if self.current_page() != "results":
                self._before_results = self.current_page()
                self.show_page("results")
        elif self.current_page() == "results":
            self.show_page(getattr(self, "_before_results", "thumbnails") or "thumbnails")

    def add_results(self, page: int, rects: list, contexts: list):
        doc = self.view.document
        for k, (rect, ctx) in enumerate(zip(rects, contexts)):
            before, match, after = ctx
            row = Gtk.ListBoxRow()
            row._target = (page, k)
            box = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4)
            label = Gtk.Label(xalign=0, hexpand=True, wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END,
                              wrap_mode=Pango.WrapMode.WORD_CHAR)
            lead = before[-30:]
            if len(before) > 30 and " " in lead:          # extrait coupé sur un mot entier
                lead = "…" + lead[lead.index(" ") + 1:]
            label.set_markup(GLib.markup_escape_text(shown(lead)) + "<b>"
                             + GLib.markup_escape_text(shown(match)) + "</b>"
                             + GLib.markup_escape_text(shown(after[:40])))
            box.append(label)
            box.append(Gtk.Label(label=shown(doc.page_label(page)) if doc else "", valign=Gtk.Align.START,
                                 css_classes=["dim-label", "numeric"]))
            row.set_child(box)
            self.results_list.append(row)
            self._result_rows.append(row)

    def set_results_status(self, text: str):
        self.results_label.set_label(text)

    def select_result(self, page: int, k: int):
        for row in self._result_rows:
            if row._target == (page, k):
                self.results_list.select_row(row)
                return

    def _result_activated(self, _lb, row):
        if self.search is not None:
            self.search.show(*row._target)
