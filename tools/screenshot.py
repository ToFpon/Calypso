#!/usr/bin/env python3
"""Capture de la fenêtre de Calypso (Gtk.WidgetPaintable), pour la documentation et les essais.

    xvfb-run -a -s "-screen 0 1400x1000x24" python3.12 tools/screenshot.py SORTIE.png [options]

  --file=PDF        document ouvert (par défaut : le livre d'essai de tests/samples.py)
  --size=LxH        taille de la fenêtre (1100x800)
  --dark            thème sombre
  --welcome         accueil (documents récents : ceux du HOME jetable)
  --sidebar=NOM     thumbnails, outline, results ; --no-sidebar pour le masquer
  --search=TEXTE    recherche lancée
  --zoom=Z          zoom libre (1.0 = 100 %) ; --fit-page
  --dual, --night, --page=N, --rotate=90
  --prefs=PAGE      Préférences (general, features) posées au centre
  --properties      Propriétés du document
  --annotate        barre d'annotation + quelques annotations (surlignage, note ouverte, ellipse)
  --form            formulaire d'essai à moitié rempli
  --delay=MS        attente avant la capture (1500)
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="calypso-shot-")
os.environ["HOME"] = TMP
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, ".config")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, ".local", "share")
os.environ["XDG_CACHE_HOME"] = os.path.join(TMP, ".cache")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Graphene, Gtk  # noqa: E402

import samples  # noqa: E402

args = {}
for a in sys.argv[2:]:
    k, _, v = a.lstrip("-").partition("=")
    args[k] = v or True
out = os.path.abspath(sys.argv[1])

from calypso.app import CalypsoApp  # noqa: E402

app = CalypsoApp()


def shot(win, delay):
    def do():
        paint = Gtk.WidgetPaintable.new(win)
        w, h = win.get_width(), win.get_height()
        snapshot = Gtk.Snapshot()
        paint.snapshot(snapshot, w, h)
        for d in Gtk.Window.list_toplevels():
            if d is not win and d.get_visible() and d.get_transient_for() is win:
                dw, dh = d.get_width(), d.get_height()
                snapshot.save()
                snapshot.translate(Graphene.Point().init(max((w - dw) / 2, 0), max((h - dh) / 2, 0)))
                Gtk.WidgetPaintable.new(d).snapshot(snapshot, dw, dh)
                snapshot.restore()
        tex = win.get_renderer().render_texture(snapshot.to_node(), None)
        tex.save_to_png(out)
        print("capture", out, w, h)
        for x in app.windows():              # pas de question « Enregistrer ? » pour une capture
            if x.document is not None:
                x.document.dirty = False
        app._quit()
        shutil.rmtree(TMP, ignore_errors=True)
        return False
    GLib.timeout_add(delay, do)


def start():
    if args.get("dark"):
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    pdf = args.get("file")
    if args.get("form"):
        pdf = os.path.join(TMP, "Inscription.pdf")
        samples.make_form(pdf)
    if not pdf:
        pdf = os.path.join(TMP, "Le chant de Calypso.pdf")
        samples.make_book(pdf)
    w, h = (int(x) for x in str(args.get("size", "1100x800")).split("x"))
    delay = int(args.get("delay", 1500))
    if args.get("welcome"):
        from calypso.backend.history import history
        for k in range(4):
            p = os.path.join(TMP, f"Document {k + 1}.pdf")
            samples.make_book(p, pages=2)
            history().remember(p, {"page": 0})
        win = app.new_window()
        win.set_default_size(w, h)
        win.welcome.refresh()
        win.present()
        shot(win, delay)
        return False
    win = app.new_window()
    win.set_default_size(w, h)
    win.present()
    win.open_document(pdf)

    def ready():
        if win.document is None:
            return True
        v = win.view
        if args.get("no-sidebar"):
            win.split.set_show_sidebar(False)
        if args.get("dual"):
            win.activate_action("win.dual", None)
        if args.get("night"):
            win.activate_action("win.night", None)
        if args.get("fit-page"):
            v.set_zoom_mode("fit-page")
        if args.get("zoom"):
            v.set_zoom(float(args["zoom"]))
        if args.get("rotate"):
            win._rotate(int(args["rotate"]))
        if args.get("page"):
            v.go_to(int(args["page"]) - 1)
        if args.get("sidebar"):
            win.sidebar.show_page(args["sidebar"])
        if args.get("search"):
            win.open_search()
            win.search.entry.set_text(args["search"])
            win.sidebar.show_page("results") if args.get("sidebar") == "results" else None
            GLib.timeout_add(700, lambda: (args.get("sidebar") == "results" and win.sidebar.show_page("results"),
                                           False)[1])
        if args.get("prefs"):
            from calypso.ui.preferences import PreferencesWindow
            d = PreferencesWindow()
            d.set_transient_for(win)
            d.set_visible_page_name(args["prefs"])
            d.present()
        if args.get("properties"):
            win.show_properties()
        if args.get("annotate"):
            win.activate_action("win.annotate", None)
            an = win.annotator
            v._set_selection((0, 60.0, 135.0), (0, 400.0, 175.0), "glyph")
            an.markup("highlight")
            an.color = (0.40, 0.70, 1.00)
            v._set_selection((0, 60.0, 250.0), (0, 300.0, 262.0), "glyph")
            an.markup("underline")
            an.color = (0.90, 0.15, 0.15)
            an.add_shape(0, "circle", (300.0, 370.0, 230.0, 70.0))
            an.color = (0.98, 0.85, 0.18)

            def note():
                an.add_note(0, 470.0, 110.0)
                if an._popover is not None:
                    an._popover.text.get_buffer().set_text("Relire ce passage avant jeudi")
                return False
            GLib.timeout_add(400, note)
        if args.get("form"):
            doc, an = win.document, win.annotator
            for f in doc.fields(0):
                if f.name == "nom":
                    an.set_field(f, "Tof")
                elif f.name == "accepte":
                    an.set_field(f, True)
            nom = [f for f in doc.fields(0) if f.name == "remarques"]
            if nom:
                GLib.timeout_add(300, lambda: (an.activate_field(nom[0]), False)[1])
        shot(win, delay)
        return False
    GLib.timeout_add(100, ready)
    return False


GLib.timeout_add(300, start)
app.run([sys.argv[0]])
