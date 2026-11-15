"""Recherche dans le document (Ctrl+F) : barre sous l'en-tête, résultats surlignés dans les pages et
listés dans le panneau latéral, comme Papers.

Le document est parcouru dans un fil, à partir de la page affichée puis en revenant au début ; les
résultats arrivent page par page. Accents et casse ignorés par défaut (Poppler), mots sur deux lignes
trouvés. Une nouvelle frappe abandonne la recherche en cours (numéro de génération).
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from ..i18n import ntr, tr  # noqa: E402


class Search:
    def __init__(self, window):
        self.window = window
        self.view = window.view
        self.sidebar = window.sidebar
        self.query = ""
        self.case = False
        self.whole = False
        self.results: dict = {}
        self.order: list = []
        self.running = False
        self._gen = 0
        self._timer = 0
        self._want_next = 0

        self.entry = Gtk.SearchEntry(placeholder_text=tr("Rechercher dans le document"), width_chars=28)
        self.entry.connect("search-changed", self._on_changed)
        self.entry.connect("activate", lambda *_: self.next())
        self.entry.connect("next-match", lambda *_: self.next())
        self.entry.connect("previous-match", lambda *_: self.previous())
        self.entry.connect("stop-search", lambda *_: self.window.close_search())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_entry_key)
        self.entry.add_controller(keys)
        self.prev_btn = Gtk.Button(icon_name="go-up-symbolic", tooltip_text=tr("Résultat précédent (Maj+Entrée)"))
        self.prev_btn.connect("clicked", lambda *_: self.previous())
        self.next_btn = Gtk.Button(icon_name="go-down-symbolic", tooltip_text=tr("Résultat suivant (Entrée)"))
        self.next_btn.connect("clicked", lambda *_: self.next())
        nav = Gtk.Box(css_classes=["linked"])
        nav.append(self.prev_btn)
        nav.append(self.next_btn)
        self.count = Gtk.Label(css_classes=["dim-label", "numeric"], width_chars=12)
        opts = Gio.Menu()
        opts.append(tr("Respecter la casse"), "win.search-case")
        opts.append(tr("Mots entiers seulement"), "win.search-whole")
        self.options = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=opts,
                                      tooltip_text=tr("Options de recherche"))
        box = Gtk.Box(spacing=6)
        box.append(self.entry)
        box.append(nav)
        box.append(self.count)
        box.append(self.options)
        self.bar = Gtk.SearchBar(child=box, show_close_button=True)
        self.bar.connect_entry(self.entry)
        self.bar.connect("notify::search-mode-enabled", self._on_mode)

    # -- options -----------------------------------------------------------------------------------
    def set_options(self, case=None, whole=None):
        if case is not None:
            self.case = case
        if whole is not None:
            self.whole = whole
        if self.query:
            self.start(self.query)

    # -- saisie ------------------------------------------------------------------------------------
    def _on_mode(self, bar, _p):
        if not bar.get_search_mode():
            self.stop()

    def _on_entry_key(self, _c, keyval, _code, state):
        from gi.repository import Gdk
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.SHIFT_MASK:
            self.previous()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up) and not state & Gdk.ModifierType.CONTROL_MASK:
            (self.next if keyval == Gdk.KEY_Down else self.previous)()
            return True
        return False

    def _on_changed(self, entry):
        if self._timer:
            GLib.source_remove(self._timer)
        text = entry.get_text()

        def go():
            self._timer = 0
            self.start(text)
            return False
        # recherche lancée à la pause de frappe, tout de suite si le champ est vidé
        self._timer = GLib.timeout_add(250 if text else 0, go)

    # -- recherche ---------------------------------------------------------------------------------
    def stop(self):
        self._gen += 1
        self.query = ""
        self.results = {}
        self.order = []
        self.running = False
        self.view.set_search_results({}, None)
        self.sidebar.set_results(None)
        self.count.set_label("")

    def start(self, query: str):
        self._gen += 1
        gen = self._gen
        self.query = query.strip()
        self.results = {}
        self.order = []
        self._want_next = 0
        self.view.set_search_results({}, None)
        doc = self.view.document
        self.sidebar.set_results(self if self.query and doc is not None else None)
        if not self.query or doc is None:
            self.running = False
            self.count.set_label("")
            return
        self.running = True
        self._update_count()
        n = doc.n_pages
        first = self.view.page
        pages = list(range(first, n)) + list(range(0, first))
        case, whole, query = self.case, self.whole, self.query

        def work():
            for i in pages:
                if gen != self._gen:
                    return
                try:
                    rects = doc.find(i, query, case, whole)
                    contexts = [doc.context(i, r) for r in rects]
                except Exception:
                    rects, contexts = [], []
                if rects:
                    GLib.idle_add(self._got, gen, i, rects, contexts)
            GLib.idle_add(self._finished, gen)
        threading.Thread(target=work, name="calypso-search", daemon=True).start()

    def _got(self, gen, page, rects, contexts):
        if gen != self._gen:
            return False
        self.results[page] = rects
        self.order = sorted((p, k) for p, rs in self.results.items() for k in range(len(rs)))
        current = self.view.search_current
        self.view.set_search_results(self.results, current)
        self.sidebar.add_results(page, rects, contexts)
        if current is None:
            self.show(page, 0)
        self._update_count()
        return False

    def _finished(self, gen):
        if gen == self._gen:
            self.running = False
            self._update_count()
        return False

    def _update_count(self):
        n = len(self.order)
        if self.running and not n:
            text = tr("Recherche…")
        elif not n:
            text = tr("Aucun résultat")
        else:
            cur = self.view.search_current
            idx = self.order.index(cur) + 1 if cur in self.order else 0
            text = tr("{0} sur {1}").format(idx, n) if idx else ntr("{0} résultat", "{0} résultats", n).format(n)
        self.count.set_label(text)
        self.sidebar.set_results_status(
            tr("Recherche…") if self.running and not n else
            ntr("{0} résultat", "{0} résultats", n).format(n) if n else tr("Aucun résultat"))
        has = n > 0
        self.prev_btn.set_sensitive(has)
        self.next_btn.set_sensitive(has)

    def show(self, page: int, k: int):
        self.view.show_result(page, k)
        self.sidebar.select_result(page, k)
        self._update_count()

    def _step(self, delta: int):
        if not self.order:
            return
        cur = self.view.search_current
        if cur in self.order:
            idx = (self.order.index(cur) + delta) % len(self.order)
        else:
            # pas encore de résultat choisi : le premier après la page affichée
            after = [t for t in self.order if t[0] >= self.view.page]
            idx = self.order.index(after[0]) if after and delta > 0 else (0 if delta > 0 else len(self.order) - 1)
        self.show(*self.order[idx])

    def next(self):
        if not self.query and self.entry.get_text().strip():
            self.start(self.entry.get_text())
            return
        self._step(1)

    def previous(self):
        self._step(-1)
