#!/usr/bin/env python3
"""Test de fumée de l'interface : pilote une vraie fenêtre (sous xvfb-run, vrai Poppler) et signale
toute exception ou tout avertissement critique de GTK.

    xvfb-run -a -s "-screen 0 1400x1000x24" python3.12 tests/ui_smoke.py   # doit finir par « Aucune erreur. »
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
TMP = tempfile.mkdtemp(prefix="calypso-ui-")
os.environ["HOME"] = os.path.join(TMP, "home")
for var, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[var] = os.path.join(TMP, "home", sub)
os.makedirs(os.environ["HOME"])

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

errors = []


def hook(t, v, tb):
    errors.append("".join(traceback.format_exception(t, v, tb)))
    sys.__excepthook__(t, v, tb)


sys.excepthook = hook

import samples  # noqa: E402
from calypso import features  # noqa: E402
from calypso.app import CalypsoApp  # noqa: E402
from calypso.backend.document import Link  # noqa: E402
from calypso.backend.history import history  # noqa: E402

BOOK = os.path.join(TMP, "Le chant & la nymphe.pdf")
samples.make_book(BOOK)
MIXED = os.path.join(TMP, "mixed.pdf")
samples.make_mixed(MIXED)
SECRET = os.path.join(TMP, "secret.pdf")
samples.make_encrypted(BOOK, SECRET, "sésame")
NOTES = os.path.join(TMP, "notes.pdf")
samples.make_book(NOTES, pages=3)
FORM = os.path.join(TMP, "formulaire.pdf")
samples.make_form(FORM)
BAD = os.path.join(TMP, "abîmé.pdf")
with open(BAD, "wb") as fh:
    fh.write(b"%PDF-1.4\nrien du tout\n")

app = CalypsoApp()
steps = []
log = []


def step(fn):
    steps.append(fn)
    return fn


class Wait:
    """Les étapes sont des générateurs : « yield Wait(s) » rend la main à la vraie boucle GLib."""
    def __init__(self, seconds=0.4, until=None):
        self.seconds = seconds
        self.until = until


CRITICALS = []
_err_r, _err_w = os.pipe()
_err_orig = os.dup(2)
os.dup2(_err_w, 2)


def _tee_stderr():
    with os.fdopen(_err_r, "rb", 0) as f:
        for line in iter(f.readline, b""):
            os.write(_err_orig, line)
            if b"-CRITICAL **" in line or b"-WARNING **" in line and b"a11y" not in line \
                    and b"does not have a minimum size" not in line:
                CRITICALS.append(line.decode("utf-8", "replace").strip())


threading.Thread(target=_tee_stderr, daemon=True).start()


def check(cond, msg):
    if not cond:
        errors.append("ÉCHEC : " + msg)
        print("✗", msg)
    else:
        print("✓", msg)


def run_all(win):
    def driver():
        for fn in steps:
            log.append(fn.__name__)
            gen = fn(win)
            if gen is not None and hasattr(gen, "__next__"):
                yield from gen
            yield Wait(0.2)

    it = driver()

    def tick():
        try:
            w = next(it)
        except StopIteration:
            app._quit()
            return False
        except Exception:
            errors.append(traceback.format_exc())
            app._quit()
            return False
        start = time.time()

        def poll():
            if w.until is not None:
                if w.until() or time.time() - start > w.seconds:
                    GLib.timeout_add(50, tick)
                    return False
                return True
            GLib.timeout_add(50, tick)
            return False
        if w.until is not None:
            GLib.timeout_add(50, poll)
        else:
            GLib.timeout_add(int(w.seconds * 1000), poll)
        return False
    tick()
    return False


def windows():
    return app.windows()


def other_toplevels(win):
    return [w for w in Gtk.Window.list_toplevels() if w.get_visible() and w is not win
            and not isinstance(w, type(win))]


def close_others(win):
    for w in other_toplevels(win):
        w.close()


# ==================================================================================================
@step
def accueil(win):
    check(win.stack.get_visible_child_name() == "welcome", "accueil au démarrage")
    check(not win.lookup_action("find").get_enabled(), "sans document : recherche grisée")
    check(not win.page_box.get_visible(), "sans document : pas de numéro de page")


@step
def ouvrir(win):
    win.open_document(BOOK)
    yield Wait(5, until=lambda: win.document is not None)
    check(win.document is not None and win.document.n_pages == 12, "document ouvert (12 pages)")
    check(win.stack.get_visible_child_name() == "document", "vue du document affichée")
    check(win.title_widget.get_title() == "Le chant & la nymphe.pdf", "titre = nom du fichier (« & » intact)")
    check(win.title_widget.get_subtitle() == "Le chant de Calypso", "sous-titre = titre du document")
    check(win.page_total.get_label() in ("sur 12", "of 12", "von 12"), "« sur 12 » à côté du numéro")
    yield Wait(3, until=lambda: len(win.view._cache) >= 1)
    check(len(win.view._cache) >= 1, "pages rendues par le fil de rendu")
    check(win.sidebar.thumb_model.get_n_items() == 12, "12 miniatures")
    yield Wait(3, until=lambda: len(win.sidebar._thumbs) >= 2)
    check(len(win.sidebar._thumbs) >= 2, "miniatures rendues")
    check(win.sidebar._buttons["outline"].get_visible(), "sommaire proposé")
    check(win.lookup_action("find").get_enabled(), "recherche active")


@step
def navigation(win):
    v = win.view
    v.next_page()
    yield Wait(0.3)
    check(v.page == 1, "page suivante")
    win.page_entry.set_text("7")
    win.page_entry.emit("activate")
    yield Wait(0.3)
    check(v.page == 6, "numéro tapé : page 7")
    check(win.page_entry.get_text() == "7", "le champ montre 7")
    check(win.lookup_action("go-back").get_enabled(), "« Retour » possible")
    win.activate_action("win.go-back", None)
    yield Wait(0.3)
    check(v.page == 1, "Alt+← : retour à la page 2")
    win.activate_action("win.go-forward", None)
    yield Wait(0.3)
    check(v.page == 6, "Alt+→ : de nouveau la page 7")
    win.page_entry.set_text("99")
    win.page_entry.emit("activate")
    yield Wait(0.2)
    check(v.page == 6 and win.page_entry.get_text() == "7", "page inexistante refusée")
    win.activate_action("win.last-page", None)
    yield Wait(0.3)
    check(v.page == 11, "dernière page")
    win.activate_action("win.first-page", None)
    yield Wait(0.3)
    check(v.page == 0, "première page")
    check(win.sidebar.thumb_sel.get_selected() == 0, "miniature suivie")
    win.sidebar.thumb_sel.set_selected(3)
    yield Wait(0.3)
    check(v.page == 3, "clic sur une miniature : page 4")


@step
def zoom(win):
    v = win.view
    fit = v.zoom
    win.activate_action("win.zoom-reset", None)
    yield Wait(0.3)
    check(abs(v.zoom - 1.0) < 1e-6 and v.zoom_mode == "free", "Ctrl+0 : 100 %")
    check(win.zoom_btn.get_label() == "100 %", "bouton : « 100 % »")
    page = v.page
    win.activate_action("win.zoom-in", None)
    yield Wait(0.4)
    check(v.zoom > 1.0, "zoom avant")
    check(v.page == page, "le zoom garde la page")
    win.activate_action("win.zoom-mode", GLib.Variant.new_string("fit-page"))
    yield Wait(0.4)
    r = v._rects[v.page]
    check(r[3] <= v.get_height(), "ajuster à la page : la page tient en hauteur")
    win.activate_action("win.zoom-mode", GLib.Variant.new_string("fit-width"))
    yield Wait(0.4)
    check(abs(v.zoom - fit) < 0.01, "ajuster à la largeur : zoom d'origine")
    for _ in range(25):
        v.zoom_in()
    yield Wait(0.6)
    check(abs(v.zoom - 16.0) < 1e-6, "zoom maximal 1600 %")
    check(not win.lookup_action("zoom-in").get_enabled(), "zoom avant grisé au maximum")
    yield Wait(2, until=lambda: any(k[0] == "v" and k[4][2] <= 1024 and k[2] > 10 for k in v._cache))
    check(any(k[0] == "v" and k[2] > 10 for k in v._cache), "très gros zoom : rendu par tuiles")
    biggest = max((t.get_width() * t.get_height() for k, t in v._cache.items() if k[2] > 10), default=0)
    check(0 < biggest <= 1024 * 1024, "tuiles de 1024 px au plus")
    v.set_zoom_mode("fit-width")
    yield Wait(0.4)


@step
def disposition(win):
    v = win.view
    win.activate_action("win.dual", None)
    yield Wait(0.4)
    r0, r1 = v._rects[0], v._rects[1]
    check(v.dual and r0[1] == r1[1] and r0[0] < r1[0], "deux pages côte à côte (1-2)")
    check(win.lookup_action("dual-cover").get_enabled(), "« Première page seule » proposée")
    win.activate_action("win.dual-cover", None)
    yield Wait(0.4)
    r0, r1, r2 = v._rects[0], v._rects[1], v._rects[2]
    check(r0[1] < r1[1] and r1[1] == r2[1], "première page seule, puis 2-3")
    win.activate_action("win.dual-cover", None)
    win.activate_action("win.dual", None)
    yield Wait(0.3)
    win.activate_action("win.continuous", None)
    yield Wait(0.4)
    shown_pages = [i for i, r in enumerate(v._rects) if r is not None]
    check(len(shown_pages) == 1, "non continu : une seule page mise en page")
    p = v.page
    for _ in range(3):              # bas de la page, puis page suivante
        v._page_step(1)
        yield Wait(0.2)
        if v.page != p:
            break
    yield Wait(0.4)
    check(v.page == p + 1, "non continu : Page↓ en bas de page = page suivante")
    win.activate_action("win.continuous", None)
    yield Wait(0.4)
    check(all(r is not None for r in v._rects), "continu : toutes les pages")
    check(v.page == p + 1, "le changement de disposition garde la page")


@step
def rotation_nuit(win):
    v = win.view
    win.activate_action("win.rotate-right", None)
    yield Wait(0.5)
    r = v._rects[0]
    check(v.rotation == 90 and r[2] > r[3], "pivoté : pages en paysage")
    yield Wait(2, until=lambda: any(k[0] == "v" and k[3] == 90 for k in v._cache))
    check(any(k[0] == "v" and k[3] == 90 for k in v._cache), "rendu pivoté")
    # aller-retour des coordonnées
    x, y = v.page_to_doc(0, 100, 200)
    px, py = v.doc_to_page(0, x, y)
    check(abs(px - 100) < 1e-6 and abs(py - 200) < 1e-6, "coordonnées de page ↔ vue (pivoté)")
    win.activate_action("win.rotate-left", None)
    yield Wait(0.4)
    check(v.rotation == 0, "retour à l'endroit")
    win.activate_action("win.night", None)
    yield Wait(0.2)
    check(v.night, "mode nuit")
    win.activate_action("win.night", None)


@step
def liens(win):
    v = win.view
    v.go_to(0)
    yield Wait(0.3)
    links = win.document.links(0)
    page_link = next(link for _r, link in links if link.kind == "page")
    v.follow(page_link)
    yield Wait(0.4)
    check(v.page == 5, "lien interne : page 6")
    seen = []
    hid = v.connect("external-link", lambda _v, link: seen.append(link.uri))
    launched = []
    real = Gtk.UriLauncher.launch
    Gtk.UriLauncher.launch = lambda self, *a: launched.append(self.get_uri())
    try:
        v.follow(Link("uri", uri="https://example.org/calypso"))
        yield Wait(0.2)
    finally:
        Gtk.UriLauncher.launch = real
        v.disconnect(hid)
    check(seen == ["https://example.org/calypso"] and launched == ["https://example.org/calypso"],
          "lien web ouvert par le navigateur")
    win.activate_action("win.go-back", None)
    yield Wait(0.3)
    check(v.page == 0, "retour après un lien")
    # survol d'un lien : aperçu de la page visée
    (x, y, w, h), _l = next((r, link) for r, link in links if link.kind == "page")
    vx, vy, _vw, vh = v.page_rect_to_view(0, (x, y, w, h))
    if vy < 0 or vy > v.get_height():
        v._set_values(None, v._vval() + vy - 200)
        yield Wait(0.3)
        vx, vy, _vw, vh = v.page_rect_to_view(0, (x, y, w, h))
    v._on_motion(None, vx + 3, vy + vh / 2)
    yield Wait(1.2, until=lambda: v._link_popover is not None)
    check(v._link_popover is not None, "aperçu du lien au survol")
    yield Wait(0.4)
    v._end_hover()
    check(v._link_popover is None, "aperçu fermé en quittant le lien")
    features.set_enabled("link_preview", False)
    v._hover_link = None
    v._on_motion(None, vx + 3, vy + vh / 2)
    yield Wait(0.8)
    check(v._link_popover is None, "aperçu coupé dans les Préférences")
    features.set_enabled("link_preview", True)
    v._end_hover()


@step
def selection(win):
    v = win.view
    v.go_to(0)
    yield Wait(0.3)
    w, h = win.document.sizes[0]
    v._set_selection((0, 60.0, 60.0), (0, 300.0, 100.0), "glyph")
    check(v.has_selection() and "Chapitre" in v.selected_text(), "texte sélectionné")
    check(win.lookup_action("copy").get_enabled(), "« Copier » actif")
    got = []
    win.activate_action("win.copy", None)
    win.get_clipboard().read_text_async(None, lambda cb, res: got.append(cb.read_text_finish(res)))
    yield Wait(2, until=lambda: got)
    check(got and "Chapitre" in (got[0] or ""), "Ctrl+C : texte dans le presse-papiers")
    v._set_selection((0, 60.0, 700.0), (1, 300.0, 100.0), "glyph")
    text = v.selected_text()
    check("Aller au chapitre" in text and "\n" in text, "sélection sur deux pages")
    win.activate_action("win.select-all", None)
    check(v._sel[1][0] == 11, "tout sélectionner : jusqu'à la dernière page")
    v.clear_selection()
    check(not v.has_selection(), "sélection effacée")


@step
def recherche(win):
    v = win.view
    v.go_to(0)
    win.activate_action("win.find", None)
    yield Wait(0.3)
    check(win.search.bar.get_search_mode(), "Ctrl+F : barre de recherche")
    win.search.entry.set_text("nymphe")
    yield Wait(5, until=lambda: win.search.order and not win.search.running)
    n = len(win.search.order)
    check(n > 10, f"résultats trouvés ({n})")
    check(win.view.search_current == win.search.order[0], "premier résultat choisi")
    check(win.search.count.get_label() in (f"1 sur {n}", f"1 of {n}", f"1 von {n}"), "compteur « 1 sur N »")
    check(len(win.sidebar._result_rows) == n, "résultats listés dans le panneau")
    win.activate_action("win.find-next", None)
    yield Wait(0.2)
    check(win.view.search_current == win.search.order[1], "Ctrl+G : résultat suivant")
    win.activate_action("win.find-previous", None)
    win.activate_action("win.find-previous", None)
    yield Wait(0.2)
    check(win.view.search_current == win.search.order[-1], "Maj+Ctrl+G : on repart de la fin")
    last_page = win.search.order[-1][0]
    check(v.page == last_page or abs(v.page - last_page) <= 1, "la vue suit le résultat")
    # Ctrl+A dans le champ : le texte du champ, pas le document
    win.search.entry.grab_focus()
    yield Wait(0.2)
    handled = win._on_capture_key(None, Gdk.KEY_a, 0, Gdk.ModifierType.CONTROL_MASK)
    check(handled and not v.has_selection(), "Ctrl+A dans la recherche : pas le document")
    win.search.entry.set_text("introuvable-xyz")
    yield Wait(5, until=lambda: not win.search.running and win.search.query == "introuvable-xyz")
    check(not win.search.order and win.search.count.get_label() in ("Aucun résultat", "No results",
                                                                    "Keine Treffer"), "aucun résultat")
    win.close_search()
    yield Wait(0.2)
    check(not v.search_results and not win.sidebar._buttons["results"].get_visible(), "recherche fermée")


@step
def menu_contextuel(win):
    v = win.view
    v.go_to(1)
    yield Wait(0.4)
    (x, y, w, h), image_id = win.document.images(1)[0]
    vx, vy, vw, vh = v.page_rect_to_view(1, (x, y, w, h))
    v.emit("context-menu", vx + 5, vy + 5, {"hit": (1, x + 5, y + 5), "link": None, "image": image_id})
    yield Wait(0.3)
    pop = getattr(v, "_calypso_popover", None)
    check(pop is not None and pop.get_visible(), "clic droit : menu")
    if pop is not None:
        pop.popdown()
    win._ctx_copy_image()
    got = []
    win.get_clipboard().read_texture_async(None, lambda cb, res: got.append(cb.read_texture_finish(res)))
    yield Wait(2, until=lambda: got)
    check(got and got[0] is not None and got[0].get_width() == 200, "« Copier l'image » : image de 200 px")


@step
def annotations(win):
    from calypso.backend.document import Document
    w = app.open_path(NOTES, new_window=True)
    yield Wait(5, until=lambda: w.document is not None)
    v, an = w.view, w.annotator
    w.activate_action("win.annotate", None)
    yield Wait(0.4)
    check(w.tools_bar.get_reveal_child(), "« Annoter » : barre d'outils")
    v._set_selection((0, 60.0, 70.0), (0, 300.0, 100.0), "glyph")
    w.activate_action("win.tool", GLib.Variant.new_string("highlight"))
    yield Wait(0.3)
    kinds = [a.kind for a in w.document.annots(0)]
    check(kinds == ["highlight"], "texte sélectionné + Surligner : surlignage posé")
    check(not v.has_selection(), "sélection effacée après le surlignage")
    check(w.title_widget.get_title().startswith("• "), "titre marqué « • » (non enregistré)")
    check(w.lookup_action("save").get_enabled() and w.lookup_action("undo").get_enabled(), "Enregistrer et Annuler actifs")
    yield Wait(2, until=lambda: any(k[1] == 0 for k in v._cache))
    w.activate_action("win.undo", None)
    yield Wait(0.2)
    check(w.document.annots(0) == [], "Ctrl+Z : surlignage retiré")
    w.activate_action("win.redo", None)
    yield Wait(0.2)
    check([a.kind for a in w.document.annots(0)] == ["highlight"], "Ctrl+Maj+Z : surlignage revenu")
    # note : bulle ouverte, texte tapé
    w.activate_action("win.tool", GLib.Variant.new_string("note"))
    an.add_note(0, 420.0, 200.0)
    yield Wait(0.4)
    pop = an._popover
    check(pop is not None and pop.get_visible(), "nouvelle note : bulle ouverte")
    if pop is not None:
        pop.text.get_buffer().set_text("Relire ce passage")
        pop.popdown()
    yield Wait(0.4)
    notes = [a for a in w.document.annots(0) if a.kind == "note"]
    check(notes and notes[0].contents == "Relire ce passage", "texte de la note enregistré dans l'annotation")
    an.add_shape(1, "circle", (100.0, 100.0, 120.0, 60.0))
    yield Wait(0.2)
    check([a.kind for a in w.document.annots(1)] == ["circle"], "ellipse tracée")
    w.sidebar.show_page("annots")
    yield Wait(0.3)
    rows = []
    c = w.sidebar.annots_list.get_first_child()
    while c is not None:
        rows.append(c)
        c = c.get_next_sibling()
    check(len(rows) == 3, f"panneau « Annotations » : 3 lignes ({len(rows)})")
    w.sidebar.show_page("thumbnails")
    # couleur changée sur l'annotation choisie, puis suppression au clavier
    circle = w.document.annots(1)[0]
    v.select_annot(circle)
    an.set_color((0.9, 0.15, 0.15))
    yield Wait(0.2)
    col = w.document.annot_props(circle.annot)["color"]
    check(col is not None and col[0] > 0.85 and col[1] < 0.2, "couleur de l'annotation choisie changée")
    v.grab_focus()
    v._on_key(None, Gdk.KEY_Delete, 0, 0)
    yield Wait(0.2)
    check(w.document.annots(1) == [], "Suppr : annotation choisie supprimée")
    w.activate_action("win.undo", None)
    yield Wait(0.2)
    check(len(w.document.annots(1)) == 1, "Ctrl+Z : annotation supprimée revenue")
    # enregistrer
    doc = w.document
    w.activate_action("win.save", None)
    yield Wait(5, until=lambda: not doc.dirty and not w._saving)
    check(not doc.dirty and not w.title_widget.get_title().startswith("•"), "Ctrl+S : enregistré, « • » retiré")
    yield Wait(1.5)
    check(w.document is doc, "notre propre enregistrement ne recharge pas le document")
    d2 = Document.open(NOTES)
    got = sorted(a.kind for p in range(3) for a in d2.annots(p))
    check(got == ["circle", "highlight", "note"], f"fichier relu : les trois annotations y sont ({got})")
    # fermeture avec une modification non enregistrée : question
    an.add_shape(2, "square", (50.0, 50.0, 40.0, 40.0))
    yield Wait(0.2)
    w.close()
    yield Wait(0.5)
    dialogs = [d for d in Gtk.Window.list_toplevels() if isinstance(d, Adw.MessageDialog) and d.get_visible()]
    check(bool(dialogs) and w.get_visible(), "fermer avec des modifications : question posée, fenêtre gardée")
    for d in dialogs:
        d.response("discard")
        d.close()
    yield Wait(0.6)
    check(not w.get_visible(), "« Ne pas enregistrer » : fenêtre fermée")
    check(len(Document.open(NOTES).annots(2)) == 0, "rien d'écrit sans « Enregistrer »")


@step
def formulaires(win):
    from calypso.backend.document import Document
    w = app.open_path(FORM, new_window=True)
    yield Wait(5, until=lambda: w.document is not None)
    yield Wait(0.5)
    check(w.forms_banner.get_revealed(), "formulaire : bandeau « à remplir »")
    doc, an, v = w.document, w.annotator, w.view
    fields = {(f.name, f.rect[0]): f for f in doc.fields(0)}
    an.activate_field(fields[("accepte", 150.0)])
    yield Wait(0.2)
    check(doc.field_value(fields[("accepte", 150.0)]) is True, "case cochée d'un clic")
    an.activate_field(fields[("taille", 200.0)])
    yield Wait(0.2)
    vals = {k: doc.field_value(f) for k, f in ((k, f) for k, f in
                                               ((k, next(x for x in doc.fields(0) if (x.name, x.rect[0]) == k))
                                                for k in fields))}
    check(vals[("taille", 200.0)] is True and vals[("taille", 150.0)] is False, "bouton radio choisi")
    nom = next(x for x in doc.fields(0) if x.name == "nom")
    an.activate_field(nom)
    yield Wait(0.3)
    ed = v.editor_widget()
    check(isinstance(ed, Gtk.Entry), "champ texte : zone de saisie posée sur le champ")
    if ed is not None:
        ed.set_text("Tof & Calypso")
        ed.emit("activate")
    yield Wait(0.3)
    nom = next(x for x in doc.fields(0) if x.name == "nom")
    check(doc.field_value(nom) == "Tof & Calypso" and v.editor_widget() is None, "Entrée : valeur écrite dans le champ")
    rem = next(x for x in doc.fields(0) if x.name == "remarques")
    an.activate_field(rem)
    yield Wait(0.3)
    ed = v.editor_widget()
    check(ed is not None and isinstance(ed.get_child(), Gtk.TextView), "champ multiligne : zone de texte")
    if ed is not None:
        ed.get_child().get_buffer().set_text("ligne 1\nligne 2")
        v.close_editor(commit=True)
    yield Wait(0.2)
    rem = next(x for x in doc.fields(0) if x.name == "remarques")
    check(doc.field_value(rem) == "ligne 1\nligne 2", "texte sur deux lignes écrit")
    pays = next(x for x in doc.fields(0) if x.name == "pays")
    an.activate_field(pays)
    yield Wait(0.3)
    check(getattr(an, "_choice", None) is not None and an._choice.get_visible(), "liste déroulante : choix proposés")
    an._choice.popdown()
    an.set_field(pays, ((1,), ""))
    w.activate_action("win.undo", None)
    yield Wait(0.2)
    pays = next(x for x in doc.fields(0) if x.name == "pays")
    check(doc.field_value(pays) == ((0,), ""), "Ctrl+Z : choix annulé")
    w.activate_action("win.redo", None)
    w.activate_action("win.save", None)
    yield Wait(5, until=lambda: not doc.dirty and not w._saving)
    d2 = Document.open(FORM)
    got = {f.name: d2.field_value(f) for f in d2.fields(0) if f.kind not in ("radio",)}
    check(got.get("nom") == "Tof & Calypso" and got.get("accepte") is True and got.get("pays") == ((1,), ""),
          f"formulaire enregistré et relu ({got})")
    w.close()
    yield Wait(0.4)


@step
def fenetres_annexes(win):
    win.activate_action("win.properties", None)
    yield Wait(0.4)
    props = other_toplevels(win)
    check(bool(props), "Propriétés du document")
    close_others(win)
    app.activate_action("preferences", None)
    yield Wait(0.4)
    check(bool(other_toplevels(win)), "Préférences")
    close_others(win)
    win.activate_action("win.shortcuts", None)
    yield Wait(0.4)
    check(any(isinstance(w, Gtk.ShortcutsWindow) for w in other_toplevels(win)), "raccourcis clavier")
    close_others(win)
    yield Wait(0.2)


@step
def rechargement(win):
    v = win.view
    v.go_to(3)
    yield Wait(0.3)
    samples.make_book(BOOK, pages=6)
    yield Wait(5, until=lambda: win.document is not None and win.document.n_pages == 6)
    check(win.document.n_pages == 6, "document modifié ailleurs : relu tout seul")
    check(v.page == 3, "rechargé à la même page")
    check(win.sidebar.thumb_model.get_n_items() == 6, "miniatures à jour")
    samples.make_book(BOOK)
    yield Wait(5, until=lambda: win.document.n_pages == 12)


@step
def reprise(win):
    win.view.go_to(8)
    win.view.set_zoom(1.5)
    yield Wait(0.4)
    win.save_state()
    st = history().state(BOOK)
    check(st.get("page") == 8 and st.get("zoom_mode") == "free", "état retenu (page 9, zoom libre)")
    win.close()
    yield Wait(0.5)
    w2 = app.open_path(BOOK)
    yield Wait(5, until=lambda: w2.document is not None)
    yield Wait(0.5)
    check(w2.view.page == 8, "rouvert à la page 9")
    check(abs(w2.view.zoom - 1.5) < 0.01, "rouvert au même zoom")
    w3 = app.open_path(BOOK)
    check(w3 is w2 and len(windows()) == 1, "déjà ouvert : la même fenêtre revient")

@step
def mot_de_passe(win):
    w = app.open_path(SECRET, new_window=True)
    yield Wait(3, until=lambda: any(isinstance(d, Adw.MessageDialog) for d in other_toplevels(w)))
    dialogs = [d for d in Gtk.Window.list_toplevels() if isinstance(d, Adw.MessageDialog) and d.get_visible()]
    check(bool(dialogs), "document protégé : mot de passe demandé")
    if not dialogs:
        return
    d = dialogs[0]
    entry = d.get_extra_child().get_first_child()
    entry.set_text("faux")
    d.response("unlock")
    d.close()
    yield Wait(3, until=lambda: any(isinstance(x, Adw.MessageDialog) and x is not d and x.get_visible()
                                    for x in Gtk.Window.list_toplevels()))
    dialogs = [x for x in Gtk.Window.list_toplevels() if isinstance(x, Adw.MessageDialog) and x.get_visible()]
    check(bool(dialogs) and "incorrect" in dialogs[0].get_body().lower() or
          bool(dialogs) and LANG_WRONG(dialogs[0].get_body()), "mauvais mot de passe : redemandé")
    if not dialogs:
        return
    d = dialogs[0]
    d.get_extra_child().get_first_child().set_text("sésame")
    d.response("unlock")
    d.close()
    yield Wait(5, until=lambda: w.document is not None)
    check(w.document is not None and w.document.n_pages == 12, "déverrouillé avec le bon mot de passe")
    w.close()
    yield Wait(0.3)


def LANG_WRONG(body):
    return any(s in body for s in ("Incorrect password", "Falsches Passwort"))


@step
def fichier_abime(win):
    w = app.open_path(BAD, new_window=True)
    yield Wait(3, until=lambda: w.stack.get_visible_child_name() == "error")
    check(w.stack.get_visible_child_name() == "error" and w.document is None, "fichier abîmé : page d'erreur")
    w.close()
    yield Wait(0.3)


@step
def pages_melangees(win):
    w = app.open_path(MIXED, new_window=True)
    yield Wait(5, until=lambda: w.document is not None)
    r = w.view._rects
    check(r[1][2] > r[1][3] and r[0][2] < r[0][3], "pages de tailles différentes (portrait, paysage)")
    w.close()
    yield Wait(0.3)


@step
def accueil_recents(win):
    w = app.new_window()
    w.present()
    yield Wait(0.5)
    names = [c.get_child()._path for c in _flow_children(w.welcome.flow)]
    check(os.path.realpath(BOOK) in names, "accueil : le livre dans les documents récents")
    yield Wait(3, until=lambda: os.path.isdir(os.path.join(os.environ["XDG_CACHE_HOME"], "calypso", "thumbs")))
    check(os.path.isdir(os.path.join(os.environ["XDG_CACHE_HOME"], "calypso", "thumbs")), "vignettes des récents")
    tile = next(c.get_child() for c in _flow_children(w.welcome.flow)
                if c.get_child()._path == os.path.realpath(BOOK))
    check(tile._close.get_opacity() == 0, "croix cachée hors survol")
    tile._close.emit("clicked")
    yield Wait(0.3)
    names = [c.get_child()._path for c in _flow_children(w.welcome.flow)]
    check(os.path.realpath(BOOK) not in names, "croix : retiré de la liste")
    toasts = []
    w.toast_overlay.add_toast = lambda t: toasts.append(t)
    w.activate_action("win.forget-recent", GLib.Variant.new_string(os.path.realpath(MIXED)))
    yield Wait(0.2)
    check(toasts and toasts[0].get_button_label(), "toast avec « Annuler »")
    if toasts:
        toasts[0].emit("button-clicked")
    yield Wait(0.3)
    names = [c.get_child()._path for c in _flow_children(w.welcome.flow)]
    check(os.path.realpath(MIXED) in names, "« Annuler » : remis dans la liste")
    w.close()
    yield Wait(0.3)


def _flow_children(flow):
    out = []
    c = flow.get_first_child()
    while c is not None:
        out.append(c)
        c = c.get_next_sibling()
    return out


def on_window(_a, w):
    if not log:
        log.append("start")
        app.hold()          # l'étape « reprise » ferme la seule fenêtre avant d'en rouvrir une
        GLib.timeout_add(500, run_all, w)


app.connect("window-added", on_window)
app.run([sys.argv[0]])
shutil.rmtree(TMP, ignore_errors=True)
sys.stderr.flush()
time.sleep(0.2)
for line in CRITICALS:
    errors.append("ÉCHEC : avertissement de GTK / GLib : " + line)
print("\n".join(errors) if errors else "\nAucune erreur.")
sys.exit(1 if errors else 0)
