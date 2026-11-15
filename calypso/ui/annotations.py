"""Annotations et formulaires : outils, bulle de modification, annuler / rétablir.

`Annotator` est le seul à modifier le document : chaque changement passe par lui, est noté dans la
pile d'annulation (Ctrl+Z / Ctrl+Maj+Z), puis la page est redessinée (`DocView.invalidate_page`)
et la fenêtre prévenue (titre « • », panneau des annotations).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from ..backend.document import default_author  # noqa: E402
from ..i18n import tr  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import shown  # noqa: E402

# couleurs proposées (celles des annotations, pas du thème : elles sont écrites dans le PDF)
COLORS = [("yellow", (0.98, 0.85, 0.18), tr("Jaune")), ("green", (0.45, 0.85, 0.35), tr("Vert")),
          ("blue", (0.40, 0.70, 1.00), tr("Bleu")), ("pink", (1.00, 0.55, 0.75), tr("Rose")),
          ("orange", (1.00, 0.60, 0.20), tr("Orange")), ("red", (0.90, 0.15, 0.15), tr("Rouge"))]

TOOLS = [("select", "calypso-pointer-symbolic", tr("Sélectionner")),
         ("note", "calypso-note-symbolic", tr("Note")),
         ("highlight", "calypso-highlight-symbolic", tr("Surligner")),
         ("underline", "format-text-underline-symbolic", tr("Souligner")),
         ("strikeout", "format-text-strikethrough-symbolic", tr("Barrer")),
         ("squiggly", "calypso-squiggly-symbolic", tr("Souligner en vague")),
         ("square", "calypso-rectangle-symbolic", tr("Rectangle")),
         ("circle", "calypso-ellipse-symbolic", tr("Ellipse"))]


def kind_label(kind: str) -> str:
    return {"note": tr("Note"), "highlight": tr("Surlignage"), "underline": tr("Soulignement"),
            "strikeout": tr("Texte barré"), "squiggly": tr("Soulignement en vague"), "square": tr("Rectangle"),
            "circle": tr("Ellipse"), "line": tr("Trait"), "freetext": tr("Texte"), "ink": tr("Dessin"),
            "stamp": tr("Tampon"), "polygon": tr("Polygone"), "polyline": tr("Ligne brisée"),
            "attachment": tr("Pièce jointe")}.get(kind, tr("Annotation"))


def kind_icon(kind: str) -> str:
    return {"note": "calypso-note-symbolic", "highlight": "calypso-highlight-symbolic",
            "underline": "format-text-underline-symbolic", "strikeout": "format-text-strikethrough-symbolic",
            "squiggly": "calypso-squiggly-symbolic", "square": "calypso-rectangle-symbolic",
            "circle": "calypso-ellipse-symbolic"}.get(kind, "document-edit-symbolic")


def merge_lines(rects: list) -> list:
    """Rectangles de la sélection de Poppler (un par morceau de ligne) → un par ligne."""
    lines: list = []
    for (x, y, w, h) in sorted(rects, key=lambda r: (r[1], r[0])):
        cy = y + h / 2
        for line in lines:
            lx, ly, lw, lh = line
            if ly <= cy <= ly + lh and x <= lx + lw + max(h, lh):
                nx, ny = min(lx, x), min(ly, y)
                line[:] = [nx, ny, max(lx + lw, x + w) - nx, max(ly + lh, y + h) - ny]
                break
        else:
            lines.append([x, y, w, h])
    return [tuple(line) for line in lines]


_css_done = False


def _swatch_css():
    global _css_done
    if _css_done:
        return
    _css_done = True
    css = "".join(
        f".calypso-swatch-{name} {{ background: rgb({int(r * 255)},{int(g * 255)},{int(b * 255)}); }}"
        for name, (r, g, b), _l in COLORS)
    css += (".calypso-swatch { min-width: 22px; min-height: 22px; padding: 0; border-radius: 999px; "
            "box-shadow: inset 0 0 0 1px alpha(black, 0.25); }"
            ".calypso-swatch.current { box-shadow: inset 0 0 0 2px alpha(black, 0.6), 0 0 0 2px @accent_color; }")
    provider = Gtk.CssProvider()
    provider.load_from_data(css.encode(), -1)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def swatches(current, on_pick) -> Gtk.Box:
    _swatch_css()
    box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
    for name, rgb, label in COLORS:
        b = Gtk.Button(css_classes=["calypso-swatch", "calypso-swatch-" + name], tooltip_text=label)
        if current is not None and all(abs(a - c) < 0.02 for a, c in zip(rgb, current)):
            b.add_css_class("current")
        b.connect("clicked", lambda _b, rgb=rgb: on_pick(rgb))
        box.append(b)
    return box


class Annotator:
    def __init__(self, window):
        self.win = window
        self.view = window.view
        self.color = COLORS[0][1]
        self._undo: list = []
        self._redo: list = []
        # une seule bulle de chaque sorte, gardée et réutilisée : une bulle détruite juste après sa
        # fermeture faisait planter GTK 4.14 (gtk_window_native_layout → gdk_surface_request_motion(NULL))
        self._popover = None                 # bulle d'annotation ouverte (None : fermée)
        self._annot_pop = None
        self._choice = None

    @property
    def doc(self):
        return self.view.document

    @property
    def author(self) -> str:
        return settings["annot_author"].strip() or default_author()

    def reset(self):
        self._undo.clear()
        self._redo.clear()
        self.close_popover()

    # -- historique -------------------------------------------------------------------------------
    def _push(self, ops: list):
        if ops:
            self._undo.append(ops)
            del self._undo[:-200]
            self._redo.clear()
        self._changed({op[1] for op in ops})

    def _changed(self, pages):
        for p in pages:
            self.view.invalidate_page(p)
        self.win.document_changed()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def _apply(self, op, reverse: bool):
        kind, page = op[0], op[1]
        doc = self.doc
        # op est une liste : une annotation remise est une copie neuve (restore_annot), qui remplace
        # l'ancienne dans l'historique
        if kind == "add":
            if reverse:
                doc.remove_annot(page, op[2])
            else:
                op[2] = doc.restore_annot(page, op[2])
        elif kind == "remove":
            if reverse:
                op[2] = doc.restore_annot(page, op[2])
            else:
                doc.remove_annot(page, op[2])
        elif kind == "props":
            doc.set_annot_props(page, op[2], op[3] if reverse else op[4])
        elif kind == "field":
            doc.set_field_value(op[2], op[3] if reverse else op[4])

    def undo(self):
        if not self._undo:
            return
        self.close_popover()
        self.view.close_editor(commit=False)
        ops = self._undo.pop()
        for op in reversed(ops):
            self._apply(op, True)
        self._redo.append(ops)
        self.view.select_annot(None)
        self._changed({op[1] for op in ops})

    def redo(self):
        if not self._redo:
            return
        self.close_popover()
        ops = self._redo.pop()
        for op in ops:
            self._apply(op, False)
        self._undo.append(ops)
        self._changed({op[1] for op in ops})

    # -- création -----------------------------------------------------------------------------------
    def markup(self, kind: str):
        view, doc = self.view, self.doc
        if doc is None or view._sel is None:
            return
        ops = []
        start, end = view._sel[0][0], view._sel[1][0]
        for page in range(start, end + 1):
            rects = merge_lines(view._selection_rects(page))
            if rects:
                a = doc.add_markup(page, kind, rects, self.color, self.author)
                if a is not None:
                    ops.append(["add", page, a])
        view.clear_selection()
        self._push(ops)

    def add_note(self, page: int, x: float, y: float):
        a = self.doc.add_note(page, max(0.0, x - 12), max(0.0, y - 12), "", self.color, self.author)
        self._push([["add", page, a]])
        info = self.doc.find_annot(a)
        if info is not None:
            self.view.select_annot(info)
            self.edit(info, new=True)

    def add_shape(self, page: int, kind: str, rect):
        a = self.doc.add_shape(page, kind, rect, self.color, self.author)
        self._push([["add", page, a]])
        info = self.doc.find_annot(a)
        self.view.select_annot(info)

    def delete(self, info):
        if info is None:
            return
        self.close_popover(commit=False)
        self.doc.remove_annot(info.page, info.annot)
        self.view.select_annot(None)
        self._push([["remove", info.page, info.annot]])

    def set_props(self, info, props: dict):
        before = self.doc.annot_props(info.annot)
        changes = {k: v for k, v in props.items() if before.get(k) != v}
        if not changes:
            return
        self.doc.set_annot_props(info.page, info.annot, changes)
        self._push([("props", info.page, info.annot, {k: before[k] for k in changes}, changes)])

    def set_color(self, rgb):
        """Couleur des prochaines annotations ; aussi celle de l'annotation choisie."""
        self.color = rgb
        if self.view.selected_annot is not None:
            self.set_props(self.view.selected_annot, {"color": rgb})

    # -- bulle de modification ----------------------------------------------------------------
    def edit(self, info, new: bool = False):
        self.close_popover()
        if self._annot_pop is None:
            self._annot_pop = AnnotPopover(self)
        self._annot_pop.load(info, new)
        self._popover = self._annot_pop
        self._annot_pop.popup()

    def close_popover(self, commit: bool = True):
        pop, self._popover = self._popover, None
        if pop is not None:
            pop.finish(commit)

    def detach(self):
        """Fenêtre fermée : bulles détachées de la vue."""
        self.close_popover(commit=False)
        for pop in (self._annot_pop, self._choice):
            if pop is not None and pop.get_parent() is not None:
                pop.unparent()
        self._annot_pop = self._choice = None

    # -- formulaires ------------------------------------------------------------------------------
    def set_field(self, field, value):
        before = self.doc.field_value(field)
        if before == value:
            return
        self.doc.set_field_value(field, value)
        self._push([("field", field.page, field, before, value)])

    def activate_field(self, field):
        doc = self.doc
        kind = field.kind
        if kind == "check":
            self.set_field(field, not doc.field_value(field))
        elif kind == "radio":
            if not doc.field_value(field):
                self.set_field(field, True)
        elif kind in ("text", "password", "file"):
            entry = Gtk.Entry(text=shown(doc.field_value(field)), visibility=kind != "password",
                              css_classes=["calypso-field"])
            entry.connect("activate", lambda *_: self.view.close_editor(commit=True))
            self._editor_keys(entry)
            entry._calypso_done = lambda ok: ok and self.set_field(field, entry.get_text())
            self.view.open_editor(entry, field.page, field.rect)
        elif kind == "multiline":
            tv = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, css_classes=["calypso-field"])
            tv.get_buffer().set_text(shown(doc.field_value(field)))
            sw = Gtk.ScrolledWindow(child=tv, hscrollbar_policy=Gtk.PolicyType.NEVER, css_classes=["calypso-field"])
            self._editor_keys(tv)
            sw.grab_focus = tv.grab_focus

            def done(ok):
                if ok:
                    b = tv.get_buffer()
                    self.set_field(field, b.get_text(b.get_start_iter(), b.get_end_iter(), False))
            sw._calypso_done = done
            self.view.open_editor(sw, field.page, field.rect)
        elif kind in ("combo", "list"):
            self._choice_popover(field)

    def _editor_keys(self, widget):
        keys = Gtk.EventControllerKey()

        def key(_c, keyval, _code, state):
            if keyval == Gdk.KEY_Escape:
                self.view.close_editor(commit=False)
                self.view.grab_focus()
                return True
            if keyval == Gdk.KEY_Tab:
                self.view.close_editor(commit=True)
                self.view.grab_focus()
                return True
            return False
        keys.connect("key-pressed", key)
        widget.add_controller(keys)

    def _choice_popover(self, field):
        doc = self.doc
        items = doc.choice_items(field)
        sel, _text = doc.field_value(field)
        if self._choice is None:
            self._choice = Gtk.Popover(has_arrow=False)
            self._choice.set_parent(self.view)
            self._choice.set_position(Gtk.PositionType.BOTTOM)
        pop = self._choice
        box = Gtk.ListBox(css_classes=["navigation-sidebar"], selection_mode=Gtk.SelectionMode.NONE)
        for k, item in enumerate(items):
            row = Gtk.ListBoxRow()
            line = Gtk.Box(spacing=8, margin_start=6, margin_end=6)
            line.append(Gtk.Label(label=shown(item) or " ", xalign=0, hexpand=True))
            check = Gtk.Image(icon_name="object-select-symbolic")
            check.set_opacity(1 if k in sel else 0)
            line.append(check)
            row.set_child(line)
            row._k = k
            box.append(row)

        def picked(_b, row):
            pop.popdown()
            self.set_field(field, ((row._k,), ""))
        box.connect("row-activated", picked)
        pop.set_child(Gtk.ScrolledWindow(child=box, propagate_natural_height=True, max_content_height=320,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER))
        x, y, w, h = self.view.page_rect_to_view(field.page, field.rect)
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(x), int(y), max(1, int(w)), max(1, int(h))
        pop.set_pointing_to(r)
        pop.popup()


class AnnotPopover(Gtk.Popover):
    """Bulle d'une annotation : texte (commentaire), couleur, supprimer. Une seule par fenêtre,
    rechargée pour chaque annotation (`load`), jamais détruite tant que la fenêtre vit."""

    def __init__(self, annotator: Annotator):
        super().__init__(autohide=True)
        self.annotator = annotator
        self.info = None
        self._done = True
        self._before = ""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6, width_request=280)
        head = Gtk.Box(spacing=8)
        self.icon = Gtk.Image()
        head.append(self.icon)
        self.title = Gtk.Label(xalign=0, hexpand=True, css_classes=["heading"])
        head.append(self.title)
        self.author = Gtk.Label(css_classes=["dim-label"], ellipsize=Pango.EllipsizeMode.END, max_width_chars=18)
        head.append(self.author)
        box.append(head)
        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=6,
                                 left_margin=6, right_margin=6, accepts_tab=False)
        self.text.update_property([Gtk.AccessibleProperty.LABEL], [tr("Commentaire")])
        sw = Gtk.ScrolledWindow(child=self.text, min_content_height=90, max_content_height=240,
                                propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                css_classes=["card"])
        box.append(sw)
        self.colors = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(self.colors)
        delete = Gtk.Button(label=tr("Supprimer"), css_classes=["destructive-action"])
        delete.connect("clicked", lambda *_: self.info is not None and annotator.delete(self.info))
        box.append(delete)
        self.set_child(box)
        self.set_parent(annotator.view)
        self.connect("closed", self._on_closed)

    def load(self, info, new: bool = False):
        from .common import clear_box
        self.info = info
        self._done = False
        props = self.annotator.doc.annot_props(info.annot)
        self._before = props["contents"]
        self.icon.set_from_icon_name(kind_icon(info.kind))
        self.title.set_label(kind_label(info.kind))
        self.author.set_label(shown(info.author))
        self.author.set_visible(bool(info.author))
        self.text.get_buffer().set_text(shown(props["contents"]))
        clear_box(self.colors)
        self.colors.append(swatches(props["color"], self._pick))
        x, y, w, h = self.annotator.view.page_rect_to_view(info.page, info.rect)
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(x), int(y), max(1, int(w)), max(1, int(h))
        self.set_pointing_to(r)
        if new or info.kind == "note":
            GLib.idle_add(lambda: (self.text.grab_focus(), False)[1])

    def _pick(self, rgb):
        self.annotator.color = rgb
        if self.info is not None:
            self.annotator.set_props(self.info, {"color": rgb})
            fresh = self.annotator.doc.find_annot(self.info.annot)
            if fresh is not None:
                self.info = fresh
                self.annotator.view.select_annot(fresh)
        self.popdown()

    def _on_closed(self, *_):
        GLib.idle_add(lambda: (self.finish(True), False)[1])

    def finish(self, commit: bool):
        if self._done:
            return
        self._done = True
        info = self.info
        if commit and info is not None and self.annotator.doc is not None:
            b = self.text.get_buffer()
            text = b.get_text(b.get_start_iter(), b.get_end_iter(), False)
            if text != self._before:
                self.annotator.set_props(info, {"contents": text})
        if self.annotator._popover is self:
            self.annotator._popover = None
        if self.get_visible():
            self.popdown()
