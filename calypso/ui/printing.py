"""Impression (Ctrl+P) par la boîte d'impression de GTK : chaque page est dessinée par Poppler
(`render_for_printing` : vectoriel, annotations imprimables comprises), à la taille du papier, centrée,
tournée d'un quart si une page en paysage tombe sur du papier en portrait (et inversement).
Réglages de la dernière impression gardés le temps de la session (imprimante, recto verso…)."""

from __future__ import annotations

import math
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..i18n import tr  # noqa: E402
from .common import alert, shown  # noqa: E402

_settings = None


def operation(window) -> Gtk.PrintOperation:
    doc = window.document
    op = Gtk.PrintOperation()
    op.set_n_pages(doc.n_pages)
    op.set_current_page(window.view.page)
    op.set_job_name(os.path.basename(window.path or "document"))
    op.set_use_full_page(False)
    op.set_embed_page_setup(True)
    op.set_allow_async(True)
    if _settings is not None:
        op.set_print_settings(_settings)

    def draw(_op, context, page):
        ctx = context.get_cairo_context()
        cw, ch = context.get_width(), context.get_height()
        pw, ph = doc.size(page)
        turn = (pw > ph) != (cw > ch)
        if turn:
            pw, ph = ph, pw
        s = min(cw / pw, ch / ph)
        ctx.translate((cw - pw * s) / 2, (ch - ph * s) / 2)
        ctx.scale(s, s)
        if turn:
            ctx.translate(pw, 0)
            ctx.rotate(math.pi / 2)
        with doc.lock:
            doc._pages[page].render_for_printing(ctx)
    op.connect("draw-page", draw)
    return op


def print_document(window):
    if window.document is None:
        return
    op = operation(window)

    def done(o, result):
        global _settings
        if result == Gtk.PrintOperationResult.APPLY:
            _settings = o.get_print_settings()
        elif result == Gtk.PrintOperationResult.ERROR:
            alert(window, tr("Impression impossible"), shown(str(o.get_error() or "")))
    op.connect("done", done)
    try:
        op.run(Gtk.PrintOperationAction.PRINT_DIALOG, window)
    except Exception as e:
        alert(window, tr("Impression impossible"), shown(str(e)))


def export_pdf(window, path: str) -> bool:
    """Même dessin, vers un fichier PDF (tests)."""
    op = operation(window)
    op.set_allow_async(False)
    op.set_export_filename(path)
    return op.run(Gtk.PrintOperationAction.EXPORT, window) == Gtk.PrintOperationResult.APPLY
