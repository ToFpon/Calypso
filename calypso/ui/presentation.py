"""Mode présentation (F5, comme Papers) : plein écran noir, une page à la fois, à la taille de l'écran.

Suivante : → ↓ Espace Page↓ Entrée n, clic gauche, molette vers le bas. Précédente : ← ↑ Page↑
Retour arrière p, clic droit, molette vers le haut. Début / Fin. « b » écran noir, « w » écran blanc.
Un numéro puis Entrée : aller à cette page. Échap, F5 ou q : sortir (la fenêtre revient à la page
présentée). Pointeur caché après 2 s sans bouger. Pages rendues dans un fil, la suivante d'avance.
Seule couleur fixe : le noir autour des pages (comme un vidéoprojecteur).
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gtk  # noqa: E402

from ..backend.render import make_texture  # noqa: E402
from ..i18n import tr  # noqa: E402

BLACK = Gdk.RGBA()
BLACK.parse("#000000")
WHITE = Gdk.RGBA()
WHITE.parse("#ffffff")


class Slide(Gtk.Widget):
    __gtype_name__ = "CalypsoSlide"

    def __init__(self, presenter):
        super().__init__(hexpand=True, vexpand=True)
        self.p = presenter

    def do_size_allocate(self, w, h, baseline):
        GLib.idle_add(lambda: (self.p._request(), False)[1])

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        r = Graphene.Rect()
        r.init(0, 0, w, h)
        p = self.p
        snap.append_color(WHITE if p.blank == "white" else BLACK, r)
        if p.blank:
            return
        x, y, pw, ph = p.page_rect(p.page, w, h)
        pr = Graphene.Rect()
        pr.init(x, y, pw, ph)
        snap.append_color(WHITE, pr)
        tex = p.textures.get((p.page, w, h))
        if tex is not None:
            snap.append_texture(tex, pr)


class PresentationWindow(Gtk.Window):
    def __init__(self, window, page: int):
        super().__init__(title=tr("Présentation"), transient_for=window, decorated=False)
        self.window = window
        self.doc = window.document
        self.page = page
        self.blank = ""
        self.textures: dict = {}
        self._typed = ""
        self._hide = 0
        self._lock = threading.Lock()
        self._wanted: list = []
        self._worker = None
        self.add_css_class("calypso-presentation")
        self.slide = Slide(self)
        self.set_child(self.slide)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._on_click)
        self.add_controller(click)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", lambda _c, dx, dy: (self.step(1 if dy > 0 else -1), True)[1])
        self.add_controller(scroll)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda *_: self._show_pointer())
        self.add_controller(motion)
        self.connect("close-request", self._on_close)
        self.fullscreen()
        self._show_pointer()

    # -- géométrie, rendu ----------------------------------------------------------------------------
    def page_rect(self, page, w, h):
        pw, ph = self.doc.size(page, self.window.view.rotation)
        s = min(w / pw, h / ph)
        dw, dh = int(pw * s), int(ph * s)
        return (w - dw) // 2, (h - dh) // 2, dw, dh

    def _request(self):
        w, h = self.slide.get_width(), self.slide.get_height()
        if w <= 0 or h <= 0:
            return
        wanted = [p for p in (self.page, self.page + 1, self.page - 1) if 0 <= p < self.doc.n_pages
                  and (p, w, h) not in self.textures]
        with self._lock:
            self._wanted = wanted
        if wanted and (self._worker is None or not self._worker.is_alive()):
            self._worker = threading.Thread(target=self._work, args=(w, h), daemon=True)
            self._worker.start()
        self.slide.queue_draw()

    def _work(self, w, h):
        ds = float(self.get_scale_factor())
        rot = self.window.view.rotation
        while True:
            with self._lock:
                if not self._wanted:
                    return
                page = self._wanted.pop(0)
            _x, _y, dw, dh = self.page_rect(page, w, h)
            pw, _ph = self.doc.size(page, rot)
            try:
                raw = self.doc.render(page, dw * ds / pw, rot, (0, 0, max(1, int(dw * ds)), max(1, int(dh * ds))))
                tex = make_texture(*raw)
            except Exception:
                continue
            GLib.idle_add(self._got, (page, w, h), tex)

    def _got(self, key, tex):
        self.textures[key] = tex
        # pas plus d'une dizaine de pages en mémoire (une page plein écran pèse plusieurs Mo)
        for k in [k for k in self.textures if abs(k[0] - self.page) > 3]:
            del self.textures[k]
        if key[0] == self.page:
            self.slide.queue_draw()
        return False

    # -- navigation ----------------------------------------------------------------------------------
    def go(self, page: int):
        page = max(0, min(self.doc.n_pages - 1, page))
        self.blank = ""
        if page != self.page:
            self.page = page
        self._request()

    def step(self, delta: int):
        if self.blank:
            self.blank = ""
            self.slide.queue_draw()
            return
        self.go(self.page + delta)

    def _on_key(self, _c, keyval, _code, state):
        k = Gdk.keyval_name(keyval) or ""
        if k.isdigit() or k.startswith("KP_") and k[3:].isdigit():
            self._typed += k[-1]
            return True
        if k in ("Return", "KP_Enter") and self._typed:
            n = int(self._typed)
            self._typed = ""
            self.go(n - 1)
            return True
        self._typed = ""
        if k in ("Right", "Down", "space", "Page_Down", "Return", "KP_Enter", "n", "KP_Page_Down"):
            self.step(1)
        elif k in ("Left", "Up", "Page_Up", "BackSpace", "p", "KP_Page_Up"):
            self.step(-1)
        elif k in ("Home", "KP_Home"):
            self.go(0)
        elif k in ("End", "KP_End"):
            self.go(self.doc.n_pages - 1)
        elif k in ("b", "B", "period"):
            self.blank = "" if self.blank == "black" else "black"
            self.slide.queue_draw()
        elif k in ("w", "W", "comma"):
            self.blank = "" if self.blank == "white" else "white"
            self.slide.queue_draw()
        elif k in ("Escape", "F5", "q"):
            self.close()
        else:
            return False
        return True

    def _on_click(self, gesture, _n, _x, _y):
        self.step(-1 if gesture.get_current_button() == 3 else 1)

    def _show_pointer(self):
        self.set_cursor_from_name("default")
        if self._hide:
            GLib.source_remove(self._hide)

        def hide():
            self._hide = 0
            self.set_cursor_from_name("none")
            return False
        self._hide = GLib.timeout_add(2000, hide)

    def _on_close(self, *_):
        if self._hide:
            GLib.source_remove(self._hide)
            self._hide = 0
        with self._lock:
            self._wanted = []
        self.window.view.go_to(self.page, remember=True)
        return False
