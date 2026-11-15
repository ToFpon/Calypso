"""Accueil d'une fenêtre sans document : ouvrir, et les documents récents en vignettes (comme Papers)."""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import APP_ID  # noqa: E402
from ..backend.history import history  # noqa: E402
from ..backend.thumbs import thumb_queue  # noqa: E402
from ..i18n import tr  # noqa: E402
from .common import clear_box, popup_menu, shown  # noqa: E402


class Welcome(Gtk.ScrolledWindow):
    def __init__(self, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.window = window
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        status = Adw.StatusPage(icon_name=APP_ID, title=tr("Ouvrir un document"),
                                description=tr("Glissez un PDF dans cette fenêtre, ou choisissez-en un"))
        status.add_css_class("compact")
        open_btn = Gtk.Button(label=tr("Ouvrir…"), halign=Gtk.Align.CENTER, action_name="win.open",
                              css_classes=["pill", "suggested-action"])
        status.set_child(open_btn)
        box.append(status)
        self.recent_title = Gtk.Label(label=tr("Documents récents"), xalign=0, css_classes=["heading"],
                                      margin_start=24, margin_bottom=6)
        box.append(self.recent_title)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, max_children_per_line=8,
                                min_children_per_line=2, column_spacing=12, row_spacing=12,
                                margin_start=18, margin_end=18, margin_bottom=24, valign=Gtk.Align.START)
        box.append(self.flow)
        clamp = Adw.Clamp(maximum_size=1100, child=box)
        self.set_child(clamp)
        self.refresh()

    def refresh(self):
        clear_box(self.flow)
        items = history().recent(24)
        self.recent_title.set_visible(bool(items))
        for path, entry in items:
            self.flow.append(self._tile(path))

    def _tile(self, path: str) -> Gtk.Widget:
        btn = Gtk.Button(css_classes=["flat", "calypso-recent"], tooltip_text=shown(path))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        frame = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.END, height_request=170,
                        css_classes=["calypso-recent-thumb"])
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, width_request=120,
                          height_request=160, valign=Gtk.Align.END)
        frame.append(pic)
        box.append(frame)
        name = Gtk.Label(label=shown(os.path.basename(path)), ellipsize=Pango.EllipsizeMode.MIDDLE,
                         max_width_chars=18, width_chars=12)
        box.append(name)
        btn.set_child(box)
        btn.connect("clicked", lambda *_: self.window.open_document(path))
        btn._path = path

        def thumb_ready(png):
            if png:
                pic.set_filename(png)
        thumb_queue.request(path, thumb_ready)
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda g, n, x, y: self._menu(btn, x, y))
        btn.add_controller(click)
        return btn

    def _menu(self, btn, x, y):
        menu = Gio.Menu()
        item = Gio.MenuItem.new(tr("Ouvrir dans une nouvelle fenêtre"), None)
        item.set_action_and_target_value("win.open-path-new", GLib.Variant.new_string(btn._path))
        menu.append_item(item)
        item = Gio.MenuItem.new(tr("Afficher dans le dossier"), None)
        item.set_action_and_target_value("win.show-path", GLib.Variant.new_string(btn._path))
        menu.append_item(item)
        item = Gio.MenuItem.new(tr("Retirer de la liste"), None)
        item.set_action_and_target_value("win.forget-recent", GLib.Variant.new_string(btn._path))
        menu.append_item(item)
        popup_menu(btn, menu, x, y)
