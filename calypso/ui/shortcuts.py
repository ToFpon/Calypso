"""Fenêtre « Raccourcis clavier » (Gtk.ShortcutsWindow décrite en XML, comme Nausicaa)."""

from __future__ import annotations

from xml.sax.saxutils import escape

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from ..i18n import tr  # noqa: E402


def _groups():
    return [
        (tr("Document"), [
            ("<Control>o", tr("Ouvrir un document")),
            ("<Control>n", tr("Nouvelle fenêtre")),
            ("<Control>s", tr("Enregistrer une copie")),
            ("<Control>r", tr("Recharger")),
            ("<Alt>Return", tr("Propriétés du document")),
            ("<Control>w", tr("Fermer la fenêtre")),
            ("<Control>q", tr("Quitter")),
        ]),
        (tr("Déplacement"), [
            ("Page_Down space", tr("Écran suivant")),
            ("Page_Up <Shift>space", tr("Écran précédent")),
            ("n p", tr("Page suivante / précédente")),
            ("Home End", tr("Première / dernière page")),
            ("<Control>l", tr("Aller à une page")),
            ("<Alt>Left <Alt>Right", tr("Retour / avance dans l'historique")),
        ]),
        (tr("Recherche et texte"), [
            ("<Control>f", tr("Rechercher")),
            ("<Control>g <Shift>F3", tr("Résultat suivant / précédent")),
            ("<Control>c", tr("Copier le texte sélectionné")),
            ("<Control>a", tr("Tout sélectionner")),
        ]),
        (tr("Affichage"), [
            ("<Control>plus <Control>minus", tr("Zoom avant / arrière")),
            ("<Control>0", tr("Taille réelle (100 %)")),
            ("w f", tr("Ajuster à la largeur / à la page")),
            ("c d", tr("Défilement continu / deux pages")),
            ("<Control>Left <Control>Right", tr("Pivoter à gauche / à droite")),
            ("<Control>i", tr("Mode nuit")),
            ("F9", tr("Panneau latéral")),
            ("F11", tr("Plein écran")),
            ("F1 <Control>question", tr("Raccourcis clavier")),
        ]),
    ]


def shortcuts_window() -> Gtk.ShortcutsWindow:
    # une seule page (max-height) : sur deux, GTK 4.14 écrit « GtkLabel reported min width -4 »
    parts = ['<interface><object class="GtkShortcutsWindow" id="win"><property name="modal">1</property>'
             '<child><object class="GtkShortcutsSection"><property name="section-name">main</property>'
             '<property name="max-height">18</property>']
    for title, rows in _groups():
        parts.append(f'<child><object class="GtkShortcutsGroup"><property name="title">{escape(title)}</property>')
        for accel, text in rows:
            parts.append('<child><object class="GtkShortcutsShortcut">')
            parts.append(f'<property name="accelerator">{escape(accel)}</property>')
            parts.append(f'<property name="title">{escape(text)}</property></object></child>')
        parts.append('</object></child>')
    parts.append('</object></child></object></interface>')
    Gio.ThemedIcon.new("x")
    builder = Gtk.Builder.new_from_string("".join(parts), -1)
    return builder.get_object("win")
