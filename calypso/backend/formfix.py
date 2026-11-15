"""Réparation des cases à cocher et boutons radio après un enregistrement par Poppler 24.02.

Formulaire en NeedAppearances : une case cochée puis redessinée reçoit de Poppler une apparence
unique (/AP /N = un dessin) au lieu de ses deux états (/AP /N << /Oui … /Off … >>) ; relu, le
fichier ne sait plus que la case était cochée (vu sur notre formulaire d'essai). Après l'écriture,
les apparences à états du fichier d'ORIGINE sont remises sur ces boutons (même page, même
rectangle), par pypdf. Sans pypdf, ou document chiffré : rien n'est touché.
"""

from __future__ import annotations

import os


def _rect_key(widget) -> tuple:
    try:
        return tuple(round(float(v), 1) for v in widget.get("/Rect", []))
    except (TypeError, ValueError):
        return ()


def _is_button(widget) -> bool:
    ft = widget.get("/FT")
    parent = widget.get("/Parent")
    if ft is None and parent is not None:
        ft = parent.get_object().get("/FT")
    return ft == "/Btn"


def _state_aps(path: str) -> dict:
    from pypdf import PdfReader
    reader = PdfReader(path)
    if reader.is_encrypted:
        return {}
    out = {}
    for i, page in enumerate(reader.pages):
        for ref in page.get("/Annots") or []:
            w = ref.get_object()
            if w.get("/Subtype") != "/Widget" or not _is_button(w):
                continue
            ap = w.get("/AP")
            normal = ap.get_object().get("/N") if ap is not None else None
            if normal is not None and hasattr(normal.get_object(), "keys") \
                    and not hasattr(normal.get_object(), "get_data"):
                out[(i, _rect_key(w))] = ap
    return out


def repair(saved: str, original: str) -> bool:
    """Remet dans `saved` les apparences à états de `original`. Vrai si le fichier a été réécrit."""
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import NameObject
    except ImportError:
        return False
    try:
        source = _state_aps(original)
        if not source:
            return False
        reader = PdfReader(saved)
        if reader.is_encrypted:
            return False
        writer = PdfWriter(clone_from=reader)
        fixed = 0
        for i, page in enumerate(writer.pages):
            for ref in page.get("/Annots") or []:
                w = ref.get_object()
                if w.get("/Subtype") != "/Widget" or not _is_button(w):
                    continue
                ap = w.get("/AP")
                normal = ap.get_object().get("/N") if ap is not None else None
                if normal is not None and not hasattr(normal.get_object(), "get_data"):
                    continue                                  # déjà à états
                good = source.get((i, _rect_key(w)))
                if good is None:
                    continue
                w[NameObject("/AP")] = good.get_object().clone(writer)
                fixed += 1
        if not fixed:
            return False
        tmp = saved + ".fix"
        with open(tmp, "wb") as fh:
            writer.write(fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, saved)
        return True
    except Exception:
        try:
            os.unlink(saved + ".fix")
        except OSError:
            pass
        return False
