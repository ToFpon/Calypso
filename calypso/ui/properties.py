"""Propriétés du document (comme Papers) : titre, auteur, dates, logiciel, format, taille des pages."""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from ..i18n import decimal, tr  # noqa: E402
from .common import DialogWindow, shown  # noqa: E402

PAPER = [("A3", 297, 420), ("A4", 210, 297), ("A5", 148, 210), ("A6", 105, 148), ("B5", 176, 250),
         (tr("Lettre US"), 215.9, 279.4), (tr("Légal US"), 215.9, 355.6), (tr("Tabloïd"), 279.4, 431.8)]


def paper_name(w_pt: float, h_pt: float) -> str:
    w, h = w_pt * 25.4 / 72, h_pt * 25.4 / 72
    size = f"{decimal(w, 0)} × {decimal(h, 0)} mm"
    for name, pw, ph in PAPER:
        for a, b in ((pw, ph), (ph, pw)):
            if abs(w - a) < 2.5 and abs(h - b) < 2.5:
                orient = tr("portrait") if h >= w else tr("paysage")
                return f"{size} ({name}, {orient})"
    return size


def human_size(n: int) -> str:
    for unit, div in ((tr("Go"), 1 << 30), (tr("Mo"), 1 << 20), (tr("ko"), 1 << 10)):
        if n >= div:
            return f"{decimal(n / div)} {unit}"
    return tr("{0} octets").format(n)


def _date(value) -> str:
    if not isinstance(value, int) or value <= 0:
        return ""
    try:
        return GLib.DateTime.new_from_unix_local(value).format("%x %X")
    except (TypeError, GLib.Error):
        return ""


class PropertiesWindow(DialogWindow):
    def __init__(self, document, path: str):
        super().__init__(title=tr("Propriétés du document"), content_width=520, content_height=600)
        info = document.info()
        page = Adw.PreferencesPage()
        doc = Adw.PreferencesGroup(title=tr("Document"))
        for label, value in ((tr("Titre"), info["title"]), (tr("Auteur"), info["author"]),
                             (tr("Sujet"), info["subject"]), (tr("Mots-clés"), info["keywords"])):
            self._row(doc, label, value)
        page.add(doc)
        made = Adw.PreferencesGroup(title=tr("Création"))
        for label, value in ((tr("Créé le"), _date(info["created"])), (tr("Modifié le"), _date(info["modified"])),
                             (tr("Créé avec"), info["creator"]), (tr("Converti en PDF par"), info["producer"])):
            self._row(made, label, value)
        page.add(made)
        tech = Adw.PreferencesGroup(title=tr("Fichier"))
        try:
            size = human_size(os.path.getsize(path))
        except OSError:
            size = ""
        w, h = document.size(0)
        for label, value in ((tr("Emplacement"), path), (tr("Taille"), size), (tr("Pages"), str(info["pages"])),
                             (tr("Format des pages"), paper_name(w, h)), (tr("Format"), info["format"]),
                             (tr("Optimisé pour le Web"), tr("Oui") if info["linearized"] else tr("Non"))):
            self._row(tech, label, value)
        page.add(tech)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        tv.set_content(page)
        self.set_child(tv)

    def _row(self, group, label, value):
        value = shown(value or "").strip()
        if not value:
            return
        row = Adw.ActionRow(title=label, use_markup=False)
        row.set_subtitle(value)
        row.set_subtitle_selectable(True)
        row.add_css_class("property")
        group.add(row)
