#!/usr/bin/env python3
"""Vrais gestes de souris et de clavier (xdotool) sur une vraie fenêtre :

    xvfb-run -a -s "-screen 0 1400x1000x24" python3.12 tests/ui_mouse.py   # « Aucune erreur. »

Glisser pour sélectionner, double-clic sur un mot, clic sur un lien, Ctrl+molette, clic droit,
touches de la vue (Page↓, n, p, Fin, Début), Ctrl+F puis Échap.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
TMP = tempfile.mkdtemp(prefix="calypso-mouse-")
os.environ["HOME"] = os.path.join(TMP, "home")
for var, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[var] = os.path.join(TMP, "home", sub)
os.makedirs(os.environ["HOME"])

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

import samples  # noqa: E402
from calypso.app import CalypsoApp  # noqa: E402

errors = []
BOOK = os.path.join(TMP, "livre.pdf")
samples.make_book(BOOK)
WM = None
if shutil.which("openbox"):
    WM = subprocess.Popen(["openbox", "--sm-disable"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)


def check(cond, msg):
    print(("✓ " if cond else "✗ ") + msg)
    if not cond:
        errors.append("ÉCHEC : " + msg)


def xdo(*args):
    subprocess.run(["xdotool", *[str(a) for a in args]], check=False)


app = CalypsoApp()


def screen_pos(win, widget, x, y):
    """Point (x, y) du widget → coordonnées de l'écran (fenêtre à l'origine sous openbox : on la place)."""
    ok, p = widget.compute_point(win, _pt(x, y))
    native = win.get_native()
    nx, ny = native.get_surface_transform()
    return int(win._origin[0] + p.x + nx), int(win._origin[1] + p.y + ny)


def _pt(x, y):
    from gi.repository import Graphene
    return Graphene.Point().init(x, y)


def scenario(win):
    v = win.view
    yield 3
    # fenêtre à un endroit connu
    out = subprocess.run(["xdotool", "search", "--name", "livre.pdf"], capture_output=True, text=True).stdout.split()
    if out:
        xdo("windowmove", out[-1], 0, 0)
        yield 1
        geo = subprocess.run(["xdotool", "getwindowgeometry", out[-1]], capture_output=True, text=True).stdout
        pos = geo.split("Position:")[1].split()[0].split(",")
        win._origin = (int(pos[0]), int(pos[1]))
    else:
        win._origin = (0, 0)
    v.go_to(0)
    yield 1
    # glisser pour sélectionner le titre « Chapitre 1 »
    x1, y1, _w, _h = v.page_rect_to_view(0, (60, 70, 1, 1))
    x2, y2, _w, _h = v.page_rect_to_view(0, (230, 95, 1, 1))
    a = screen_pos(win, v, x1, y1)
    b = screen_pos(win, v, x2, y2)
    xdo("mousemove", *a)
    yield 0.3
    xdo("mousedown", 1)
    yield 0.1
    for k in range(1, 11):
        xdo("mousemove", a[0] + (b[0] - a[0]) * k // 10, a[1] + (b[1] - a[1]) * k // 10)
        time.sleep(0.03)
    yield 0.2
    xdo("mouseup", 1)
    yield 0.4
    check("Chapitre" in v.selected_text(), "glisser : « Chapitre » sélectionné")
    # double-clic sur un mot
    wx, wy, _w, _h = v.page_rect_to_view(0, (100, 82, 1, 1))
    xdo("mousemove", *screen_pos(win, v, wx, wy))
    yield 0.3
    xdo("click", "--repeat", 2, "--delay", 80, 1)
    yield 0.5
    check(v.selected_text().strip() == "Chapitre", "double-clic : le mot")
    # clic ailleurs : sélection effacée
    ex, ey, _w, _h = v.page_rect_to_view(0, (500, 40, 1, 1))
    xdo("mousemove", *screen_pos(win, v, ex, ey))
    xdo("click", 1)
    yield 0.4
    check(not v.has_selection(), "clic : sélection effacée")
    # clic sur le lien interne (page 6)
    (lx, ly, lw, lh), _l = next((r, link) for r, link in win.document.links(0) if link.kind == "page")
    vx, vy, vw, vh = v.page_rect_to_view(0, (lx, ly, lw, lh))
    if vy + vh > v.get_height() - 10:
        v._set_values(None, v._vval() + vy - 300)
        yield 0.5
        vx, vy, vw, vh = v.page_rect_to_view(0, (lx, ly, lw, lh))
    xdo("mousemove", *screen_pos(win, v, vx + 5, vy + vh / 2))
    yield 0.3
    xdo("click", 1)
    yield 0.6
    check(v.page == 5, "clic sur le lien : page 6")
    # touches de la vue
    v.grab_focus()
    yield 0.2
    xdo("key", "End")
    yield 0.5
    check(v.page == 11, "Fin : dernière page")
    xdo("key", "Home")
    yield 0.5
    check(v.page == 0, "Début : première page")
    xdo("key", "n")
    yield 0.5
    check(v.page == 1, "n : page suivante")
    xdo("key", "p")
    yield 0.5
    check(v.page == 0, "p : page précédente")
    # Ctrl+molette : zoom
    z = v.zoom
    cx, cy = screen_pos(win, v, v.get_width() / 2, v.get_height() / 2)
    xdo("mousemove", cx, cy)
    yield 0.2
    xdo("keydown", "ctrl")
    xdo("click", 4)
    xdo("click", 4)
    xdo("keyup", "ctrl")
    yield 0.6
    check(v.zoom > z * 1.1, f"Ctrl+molette : zoom avant ({z:.2f} → {v.zoom:.2f})")
    xdo("key", "ctrl+0")
    yield 0.4
    check(abs(v.zoom - 1.0) < 1e-6, "Ctrl+0 : 100 %")
    # clic droit : menu
    xdo("click", 3)
    yield 0.5
    pop = getattr(v, "_calypso_popover", None)
    check(pop is not None and pop.get_visible(), "clic droit : menu contextuel")
    xdo("key", "Escape")
    yield 0.4
    # Ctrl+F, frappe, Échap
    xdo("key", "ctrl+f")
    yield 0.4
    check(win.search.bar.get_search_mode(), "Ctrl+F : recherche")
    xdo("type", "grotte")
    yield 1.5
    check(win.search.query == "grotte" and win.search.order, "frappe : résultats")
    xdo("key", "ctrl+a")
    yield 0.3
    check(not v.has_selection(), "Ctrl+A dans le champ : pas tout le document")
    xdo("key", "Escape")
    yield 0.4
    check(not win.search.bar.get_search_mode(), "Échap : recherche fermée")
    xdo("key", "ctrl+a")
    yield 0.3
    check(v.has_selection(), "Ctrl+A dans la vue : tout le document")
    xdo("key", "F11")
    yield 0.8
    check(win.is_fullscreen() and not win.toolbar.get_reveal_top_bars(), "F11 : plein écran, en-tête caché")
    xdo("key", "Escape")
    yield 0.8
    check(not win.is_fullscreen(), "Échap : sortie du plein écran")


def start(win):
    gen = scenario(win)

    def tick():
        try:
            delay = next(gen)
        except StopIteration:
            app._quit()
            return False
        except Exception:
            errors.append(traceback.format_exc())
            app._quit()
            return False
        GLib.timeout_add(int(delay * 1000), tick)
        return False
    GLib.timeout_add(500, tick)


def go():
    w = app.open_path(BOOK)
    w.set_default_size(1100, 850)
    start(w)
    return False


GLib.timeout_add(300, go)
app.run([sys.argv[0]])
if WM is not None:
    WM.terminate()
shutil.rmtree(TMP, ignore_errors=True)
print("\n".join(errors) if errors else "\nAucune erreur.")
sys.exit(1 if errors else 0)
