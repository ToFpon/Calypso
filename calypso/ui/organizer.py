"""Organiser les pages : grille des miniatures, glisser pour changer l'ordre, pivoter, supprimer,
dupliquer ; puis « Remplacer le document » (une copie de l'original est gardée pour « Annuler »)
ou « Enregistrer sous ». Extraire la sélection, éclater en pages séparées. Ce que Papers ne fait pas ;
le moteur est celui des « Pages » d'Odysseus (qpdf, une seule commande)."""

from __future__ import annotations

import os
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from ..backend import pdftools  # noqa: E402
from ..backend.render import make_texture  # noqa: E402
from ..i18n import ntr, tr  # noqa: E402
from ..settings import CACHE_DIR  # noqa: E402
from .common import AlertDialog, choose_folder, choose_save, clear_box, shown  # noqa: E402
from .progress import run_job  # noqa: E402

THUMB = 130
UNDO_DIR = os.path.join(CACHE_DIR, "undo")


class PageOrganizer(Adw.Window):
    def __init__(self, window):
        super().__init__(title=tr("Organiser les pages"), modal=True, transient_for=window,
                         default_width=min(1100, max(760, window.get_width())),
                         default_height=min(820, max(600, window.get_height())))
        self.window = window
        self.doc = window.document
        self.items = [[i, 0] for i in range(self.doc.n_pages)]     # [page d'origine, rotation ajoutée]
        self._thumbs: dict = {}
        self._wanted: list = []
        self._stop = False
        self._worker = None
        self._changed = False

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label=tr("Annuler"))
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        apply = Adw.SplitButton(label=tr("Remplacer le document"), css_classes=["suggested-action"])
        menu = Gio.Menu()
        menu.append(tr("Enregistrer sous…"), "org.save-as")
        apply.set_menu_model(menu)
        apply.connect("clicked", lambda *_: self.apply_in_place())
        self.apply_btn = apply
        header.pack_end(apply)
        tv.add_top_bar(header)

        bar = Gtk.Box(spacing=6, margin_start=8, margin_end=8, margin_top=4, margin_bottom=4)
        self.sel_buttons = []
        for icon, tip, cb in (("object-rotate-left-symbolic", tr("Pivoter à gauche"), lambda: self.rotate(-90)),
                              ("object-rotate-right-symbolic", tr("Pivoter à droite"), lambda: self.rotate(90)),
                              ("edit-copy-symbolic", tr("Dupliquer"), self.duplicate),
                              ("user-trash-symbolic", tr("Supprimer (Suppr)"), self.delete)):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", lambda *_a, cb=cb: cb())
            bar.append(b)
            self.sel_buttons.append(b)
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        extract = Gtk.Button(label=tr("Extraire la sélection…"))
        extract.connect("clicked", lambda *_: self.extract())
        self.sel_buttons.append(extract)
        bar.append(extract)
        split = Gtk.Button(label=tr("Éclater en pages séparées…"))
        split.connect("clicked", lambda *_: self.split())
        bar.append(split)
        bar.append(Gtk.Box(hexpand=True))
        self.info = Gtk.Label(css_classes=["dim-label"])
        bar.append(self.info)
        tv.add_top_bar(bar)

        # sélection gérée par nous (clic, Ctrl+clic, Maj+clic) : en sélection MULTIPLE, la FlowBox prend
        # le glisser pour son cadre de sélection et les miniatures ne partaient jamais
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, max_children_per_line=12,
                                min_children_per_line=2, row_spacing=12, column_spacing=12, margin_top=12,
                                margin_bottom=12, margin_start=12, margin_end=12, valign=Gtk.Align.START)
        self.sel: set = set()
        self._anchor = 0
        tv.set_content(Gtk.ScrolledWindow(child=self.flow, vexpand=True))
        self.set_content(tv)

        group = Gio.SimpleActionGroup()
        a = Gio.SimpleAction.new("save-as", None)
        a.connect("activate", lambda *_: self.save_as())
        group.add_action(a)
        self.insert_action_group("org", group)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("close-request", self._on_close)
        self._fill()

    # -- grille -------------------------------------------------------------------------------------
    def _card(self, pos: int) -> Gtk.Widget:
        src, rot = self.items[pos]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["calypso-org-card"])
        pic = Gtk.Picture(width_request=THUMB, height_request=THUMB, can_shrink=True,
                          content_fit=Gtk.ContentFit.CONTAIN)
        tex = self._thumbs.get((src, rot))
        if tex is not None:
            pic.set_paintable(tex)
        else:
            self._wanted.append((src, rot))
        box.append(pic)
        label = str(pos + 1) if src == pos else f"{pos + 1}  ({tr('page {0}').format(src + 1)})"
        box.append(Gtk.Label(label=label, css_classes=["caption"]))
        box._pic = pic
        box._key = (src, rot)
        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        drag.connect("prepare", lambda *_a, b=box: Gdk.ContentProvider.new_for_value(f"calypso-page:{self._pos(b)}"))
        box.add_controller(drag)
        drop = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
        drop.connect("drop", lambda _t, v, x, y, b=box: self._drop(v, b, x))
        box.add_controller(drop)
        click = Gtk.GestureClick()
        click.connect("released", lambda g, n, x, y, b=box: self._clicked(b, g.get_current_event_state()))
        box.add_controller(click)
        return box

    def _clicked(self, card, state):
        pos = self._pos(card)
        if pos < 0:
            return
        if state & Gdk.ModifierType.SHIFT_MASK:
            a, b = sorted((self._anchor, pos))
            self.sel = set(range(a, b + 1))
        elif state & Gdk.ModifierType.CONTROL_MASK:
            self.sel ^= {pos}
            self._anchor = pos
        else:
            self.sel = {pos}
            self._anchor = pos
        self._show_selection()

    def select(self, positions):
        self.sel = set(p for p in positions if 0 <= p < len(self.items))
        self._show_selection()

    def _show_selection(self):
        child = self.flow.get_first_child()
        while child is not None:
            (child.add_css_class if child.get_index() in self.sel else child.remove_css_class)("calypso-picked")
            child = child.get_next_sibling()
        self._sync()

    def _pos(self, card) -> int:
        child = card.get_parent()
        return child.get_index() if child is not None else -1

    def _fill(self, select=()):
        clear_box(self.flow)
        self._wanted = []
        for pos in range(len(self.items)):
            self.flow.append(self._card(pos))
        self._start_thumbs()
        self.select(select)

    def _start_thumbs(self):
        if self._worker is not None and self._worker.is_alive():
            return
        doc = self.doc

        def work():
            while not self._stop:
                try:
                    src, rot = self._wanted.pop(0)
                except IndexError:
                    return
                if (src, rot) in self._thumbs:
                    continue
                try:
                    w, h = doc.size(src, rot)
                    s = THUMB / max(w, h)
                    raw = doc.render(src, s, rot, (0, 0, max(1, int(w * s)), max(1, int(h * s))))
                    tex = make_texture(*raw)
                except Exception:
                    continue
                GLib.idle_add(self._got, (src, rot), tex)
        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def _got(self, key, tex):
        self._thumbs[key] = tex
        child = self.flow.get_first_child()
        while child is not None:
            card = child.get_child()
            if card._key == key:
                card._pic.set_paintable(tex)
            child = child.get_next_sibling()
        return False

    def selected(self) -> list:
        return sorted(self.sel)

    def _sync(self):
        sel = self.selected()
        for b in self.sel_buttons:
            b.set_sensitive(bool(sel))
        n = len(self.items)
        text = ntr("{0} page", "{0} pages", n).format(n)
        if sel:
            text += " · " + ntr("{0} choisie", "{0} choisies", len(sel)).format(len(sel))
        self.info.set_label(text)
        self.apply_btn.set_sensitive(bool(self.items))

    # -- modifications ------------------------------------------------------------------------------
    def rotate(self, delta: int):
        sel = self.selected()
        for pos in sel:
            self.items[pos][1] = (self.items[pos][1] + delta) % 360
        self._changed = True
        self._fill(sel)

    def duplicate(self):
        sel = self.selected()
        for k, pos in enumerate(sel):
            self.items.insert(pos + 1 + k, list(self.items[pos + k]))
        self._changed = True
        self._fill([pos + 1 + k for k, pos in enumerate(sel)])

    def delete(self):
        sel = set(self.selected())
        if not sel:
            return
        if len(sel) == len(self.items):
            self.window.get_display().beep()
            return
        self.items = [it for pos, it in enumerate(self.items) if pos not in sel]
        self._changed = True
        first = min(sel)
        self._fill([min(first, len(self.items) - 1)])

    def move(self, positions: list, target: int):
        """Déplace les pages `positions` (dans l'ordre) devant la position `target`."""
        moving = [self.items[p] for p in positions]
        rest = [it for k, it in enumerate(self.items) if k not in set(positions)]
        before = sum(1 for p in positions if p < target)
        at = max(0, min(len(rest), target - before))
        self.items = rest[:at] + moving + rest[at:]
        self._changed = True
        self._fill(range(at, at + len(moving)))

    def _drop(self, value: str, card, x: float) -> bool:
        if not isinstance(value, str) or not value.startswith("calypso-page:"):
            return False
        src = int(value.split(":", 1)[1])
        target = self._pos(card)
        if target < 0:
            return False
        if x > card.get_width() / 2:
            target += 1                         # moitié droite de la carte : après elle
        sel = self.selected()
        positions = sel if src in sel else [src]
        if positions == [target] or positions == [target - 1]:
            return False
        self.move(positions, target)
        return True

    def _on_key(self, _c, keyval, _code, state):
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            self.delete()
            return True
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_a, Gdk.KEY_A) and state & Gdk.ModifierType.CONTROL_MASK:
            self.select(range(len(self.items)))
            return True
        return False

    def _order(self, positions=None) -> list:
        items = self.items if positions is None else [self.items[p] for p in positions]
        return [(src, rot) for src, rot in items]

    # -- résultats ------------------------------------------------------------------------------------
    def apply_in_place(self):
        """Remplace le document ; l'original est d'abord copié dans ~/.cache/calypso/undo pour
        « Annuler » (toast de la fenêtre)."""
        if not self._changed:
            self.close()
            return
        w = self.window
        src, pw, order = w.path, w.password, self._order()
        backup = os.path.join(UNDO_DIR, f"{int(time.time())}-{os.path.basename(src)}")

        def work(job):
            import shutil
            os.makedirs(UNDO_DIR, mode=0o700, exist_ok=True)
            shutil.copy2(src, backup)
            return pdftools.arrange(job, src, order, src, pw, replace=True)
        self._changed = False
        self.close()
        run_job(w, tr("Réorganisation des pages"), work,
                lambda job: w.reopen_after_tool(pw, tr("Pages réorganisées"), backup=backup))

    def save_as(self):
        w = self.window
        src, pw, order = w.path, w.password, self._order()

        def chosen(dst):
            if not dst.lower().endswith(".pdf"):
                dst += ".pdf"
            self._changed = False
            self.close()
            # « Enregistrer sous » vers le fichier ouvert lui-même : remplacement voulu
            replace = os.path.abspath(dst) == os.path.abspath(src)
            run_job(w, tr("Réorganisation des pages"),
                    lambda j: pdftools.arrange(j, src, order, dst, pw, replace=replace),
                    lambda job: w.reopen_after_tool(pw, tr("Pages réorganisées")) if replace
                    else w.tool_result(job.result))
        choose_save(self, tr("Enregistrer sous"), pdftools.suggest_output(src, tr("pages")), chosen)

    def extract(self):
        sel = self.selected()
        if not sel:
            return
        w = self.window
        src, pw, order = w.path, w.password, self._order(sel)

        def chosen(dst):
            if not dst.lower().endswith(".pdf"):
                dst += ".pdf"
            run_job(w, tr("Extraction de {0} pages").format(len(order)),
                    lambda j: pdftools.arrange(j, src, order, dst, pw), lambda job: w.tool_result(job.result))
        choose_save(self, tr("Extraire la sélection"), pdftools.suggest_output(src, tr("extrait")), chosen)

    def split(self):
        w = self.window
        src, pw = w.path, w.password

        def chosen(folder):
            run_job(w, tr("Éclatement de {0}").format(shown(os.path.basename(src))),
                    lambda j: pdftools.split_pages(j, src, folder, 1, pw),
                    lambda job: w.tool_result(job.result[0] if job.result else "",
                                              ntr("{0} fichier créé", "{0} fichiers créés",
                                                  len(job.result)).format(len(job.result)), folder=folder))
        choose_folder(self, tr("Dossier où poser les pages"), os.path.dirname(src), chosen)

    def _on_close(self, *_):
        if self._changed:
            d = AlertDialog(tr("Abandonner les changements ?"),
                            tr("L'ordre et la rotation des pages ne seront pas appliqués."))
            d.add_response("keep", tr("Continuer"))
            d.add_response("drop", tr("Abandonner"))
            d.set_response_appearance("drop", Adw.ResponseAppearance.DESTRUCTIVE)
            d.set_close_response("keep")

            def done(_d, r):
                if r == "drop":
                    self._changed = False
                    self.close()
            d.connect("response", done)
            d.present(self)
            return True
        self._stop = True
        return False
