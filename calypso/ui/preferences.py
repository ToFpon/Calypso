"""Préférences (Adw.PreferencesWindow : vraie fenêtre, le thème du système s'y applique)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import features  # noqa: E402
from ..i18n import LANGUAGE_NAMES, tr  # noqa: E402
from ..backend.document import default_author  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import combo_row  # noqa: E402


def apply_theme():
    Adw.StyleManager.get_default().set_color_scheme({
        "light": Adw.ColorScheme.FORCE_LIGHT,
        "dark": Adw.ColorScheme.FORCE_DARK,
    }.get(settings["theme"], Adw.ColorScheme.DEFAULT))


def _choice(title: str, key: str, labels: list[str], values: list, subtitle: str = "") -> Adw.ComboRow:
    cur = settings[key]
    row = combo_row(title, labels, values.index(cur) if cur in values else 0, subtitle)
    row.connect("notify::selected", lambda r, *_: settings.__setitem__(key, values[r.get_selected()]))
    return row


def _switch(title: str, key: str, subtitle: str = "") -> Adw.SwitchRow:
    row = Adw.SwitchRow(title=title, active=bool(settings[key]))
    if subtitle:
        row.set_subtitle(subtitle)
    row.connect("notify::active", lambda r, *_: settings.__setitem__(key, r.get_active()))
    return row


def default_app_row(title: str, what: str, app_name: str) -> Adw.SwitchRow:
    """Interrupteur « application par défaut » (defaultapp.py), comme celui d'Odysseus : l'état affiché est
    celui du système ; grisé quand l'application tourne depuis les sources (pas de lanceur installé)."""
    from .. import defaultapp
    row = Adw.SwitchRow(title=title, active=defaultapp.is_default())
    row.set_subtitle(what + "\n" + defaultapp.COMMAND)
    row.set_subtitle_lines(0)
    if not defaultapp.installed():
        row.set_subtitle(row.get_subtitle() + "\n" + tr("{0} n'est pas installé (lancé depuis les sources)").format(app_name))
        row.set_sensitive(False)
        return row

    def toggled(r, *_):
        if r.get_active() != defaultapp.is_default() and not defaultapp.set_default(r.get_active()):
            r.set_active(defaultapp.is_default())
    row.connect("notify::active", toggled)
    return row


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self):
        super().__init__(title=tr("Préférences"), search_enabled=False, default_width=620, default_height=620)
        self.add(self._general())
        self.add(self._features())

    def _general(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=tr("Général"), icon_name="preferences-system-symbolic", name="general")
        system = Adw.PreferencesGroup()
        system.add(default_app_row(tr("Visionneuse PDF par défaut"),
                                    tr("Les PDF s'ouvrent avec Calypso ; coupé, l'application d'avant reprend la main."),
                                    "Calypso"))
        page.add(system)
        look = Adw.PreferencesGroup(title=tr("Apparence"))
        theme = _choice(tr("Style"), "theme", [tr("Comme le système"), tr("Clair"), tr("Sombre")],
                        ["system", "light", "dark"])
        theme.connect("notify::selected", lambda *_: apply_theme())
        look.add(theme)
        codes = list(LANGUAGE_NAMES)
        look.add(_choice(tr("Langue"), "language", [tr("Comme le système")] + [LANGUAGE_NAMES[c] for c in codes[1:]],
                         codes, tr("Appliquée au prochain démarrage")))
        page.add(look)
        new = Adw.PreferencesGroup(title=tr("Nouveaux documents"),
                                   description=tr("Réglages d'un document ouvert pour la première fois ; "
                                                  "ensuite, chaque document garde les siens"))
        new.add(_choice(tr("Zoom"), "zoom_mode", [tr("Ajuster à la largeur"), tr("Ajuster à la page")],
                        ["fit-width", "fit-page"]))
        new.add(_switch(tr("Défilement continu"), "continuous"))
        new.add(_switch(tr("Deux pages côte à côte"), "dual"))
        new.add(_switch(tr("Mode nuit"), "night", tr("Couleurs inversées : texte clair sur fond sombre")))
        page.add(new)
        notes = Adw.PreferencesGroup(title=tr("Annotations"))
        author = Adw.EntryRow(title=tr("Auteur des annotations"), text=settings["annot_author"],
                              show_apply_button=True)
        author.connect("apply", lambda r: settings.__setitem__("annot_author", r.get_text().strip()))
        notes.add(author)
        hint = Gtk.Label(label=tr("Vide : le nom de la session ({0})").format(default_author()), xalign=0,
                         wrap=True, css_classes=["dim-label", "caption"], margin_top=6)
        notes.add(hint)
        page.add(notes)
        return page

    def _features(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=tr("Fonctionnalités"), icon_name="application-x-addon-symbolic",
                                   name="features")
        group = Adw.PreferencesGroup(description=tr("Chaque fonctionnalité s'active ou se coupe tout de "
                                                    "suite, sans redémarrer Calypso"))
        self.feature_rows = {}
        for f in features.FEATURES:
            row = Adw.SwitchRow(title=f.title, subtitle=f.description, active=features.enabled(f.id))
            row.add_prefix(Gtk.Image(icon_name=f.icon))
            row.connect("notify::active", lambda r, *_, fid=f.id: features.set_enabled(fid, r.get_active()))
            self.feature_rows[f.id] = row
            group.add(row)
        page.add(group)
        return page
