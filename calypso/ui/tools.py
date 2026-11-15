"""Fenêtres des outils (reprises d'Odysseus `ui/pdf_dialogs.py`, appliquées au document ouvert) :
compresser, fusionner, filigrane, protéger par un mot de passe, retirer la protection.

Le résultat est un NOUVEAU fichier posé à côté (« nom (compressé).pdf »…, jamais par-dessus un
fichier existant) que le toast propose d'ouvrir ; seule la protection peut, sur demande, remplacer
le document (et Calypso le relit avec le nouveau mot de passe)."""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

from ..backend import pdftools  # noqa: E402
from ..backend.document import Document  # noqa: E402
from ..backend.jobs import human_size  # noqa: E402
from ..i18n import ntr, tr  # noqa: E402
from .common import alert, choose_files, choose_save, combo_row, dialog_shell, path_row, shown  # noqa: E402
from .progress import run_job  # noqa: E402

PDF_FILTER = [(tr("Documents PDF"), ["*.pdf", "*.PDF"])]


def page_texture(path: str, width: int, page: int, password: str, callback):
    """Rendu d'une page dans un fil (vignette, aperçu du filigrane) → callback(texture, taille, brut)
    où brut = (octets ARGB32, largeur, hauteur, pas)."""
    def work():
        tex, size, raw = None, (595.0, 842.0), None
        try:
            d = Document.open(path, password or None)
            w, h = d.size(page)
            s = width / w
            raw = d.render(page, s, 0, (0, 0, int(w * s), int(h * s)))
            from ..backend.render import make_texture
            tex, size = make_texture(*raw), (w, h)
        except Exception:
            pass
        GLib.idle_add(lambda: (callback(tex, size, raw), False)[1])
    threading.Thread(target=work, daemon=True).start()


def _save_as_row(dialog, title, path):
    return path_row(title, path, lambda r: choose_save(dialog, tr("Enregistrer le PDF"), r.get_text(), r.set_text),
                    icon="document-save-symbolic")


def _out(row) -> str:
    out = row.get_text().strip()
    return out if out.lower().endswith(".pdf") else out + ".pdf"


# ---------------------------------------------------------------------------------------------
# Compresser
# ---------------------------------------------------------------------------------------------

class CompressDialog:
    def __init__(self, window):
        self.window = window
        path = window.path
        self.dialog, page, action = dialog_shell(tr("Compresser le PDF"), tr("Compresser"), 540, 480)
        g = Adw.PreferencesGroup()
        row = Adw.ActionRow(title=shown(os.path.basename(path)), use_markup=False)
        row.set_subtitle(human_size(os.path.getsize(path)))
        row.add_prefix(Gtk.Image(icon_name="x-office-document-symbolic"))
        g.add(row)
        page.add(g)
        g = Adw.PreferencesGroup(title=tr("Réglages"))
        self.level = combo_row(tr("Qualité"), [label for _k, label in pdftools.GS_LEVELS], 1)
        g.add(self.level)
        self.gray = Adw.SwitchRow(title=tr("Convertir en niveaux de gris"), subtitle=tr("Encore plus léger"))
        g.add(self.gray)
        self.out = _save_as_row(self.dialog, tr("Enregistrer sous"), pdftools.suggest_output(path, tr("compressé")))
        g.add(self.out)
        page.add(g)
        action.connect("clicked", self._go)

    def _go(self, *_):
        w = self.window
        level = pdftools.GS_LEVELS[self.level.get_selected()][0]
        gray, src, pw, dst = self.gray.get_active(), w.path, w.password, _out(self.out)
        self.dialog.close()

        def done(job):
            r = job.result
            if r.kept_original:
                alert(w, tr("Rien à gagner"), tr("Ce document est déjà optimisé ({0}) : aucun fichier créé.")
                      .format(human_size(r.before)))
                return
            gain = 100 - 100 * r.after / max(r.before, 1)
            w.tool_result(r.dst, tr("{0} → {1} (−{2} %)").format(human_size(r.before), human_size(r.after),
                                                                   f"{gain:.0f}"))
        run_job(w, tr("Compression de {0}").format(shown(os.path.basename(src))),
                lambda j: pdftools.compress(j, src, dst, level, gray, pw), done)

    def present(self):
        self.dialog.present(self.window)


# ---------------------------------------------------------------------------------------------
# Fusionner
# ---------------------------------------------------------------------------------------------

class _MergeRow(Adw.ActionRow):
    def __init__(self, dialog: "MergeDialog", path: str, password: str = ""):
        super().__init__(title=shown(os.path.basename(path)), use_markup=False)
        self.set_subtitle(shown(os.path.dirname(path)))
        self.path, self.password, self.dialog = path, password, dialog
        self.set_title_lines(1)
        self.set_subtitle_lines(1)
        self.add_prefix(Gtk.Image(icon_name="list-drag-handle-symbolic", css_classes=["dim-label"]))
        self.thumb = Gtk.Picture(width_request=34, height_request=46, can_shrink=True,
                                 content_fit=Gtk.ContentFit.CONTAIN)
        self.add_prefix(self.thumb)
        page_texture(path, 68, 0, password, lambda tex, _s, _r: tex is not None and self.thumb.set_paintable(tex))
        try:
            self.n = pdftools.page_count(path, password)
        except Exception:
            self.n = 0
        self.pages = Gtk.Entry(placeholder_text=tr("toutes ({0} p.)").format(self.n), width_chars=12,
                               valign=Gtk.Align.CENTER, tooltip_text=tr("Pages à garder, ex. 1-3, 5, 8-"))
        self.pages.connect("changed", lambda *_: dialog.refresh())
        self.add_suffix(self.pages)
        for icon, step, tip in (("go-up-symbolic", -1, tr("Déplacer vers le haut")),
                                ("go-down-symbolic", 1, tr("Déplacer vers le bas"))):
            b = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text=tip)
            b.connect("clicked", lambda *_a, s=step: dialog.move(self, s))
            self.add_suffix(b)
        rm = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                        tooltip_text=tr("Retirer"))
        rm.connect("clicked", lambda *_: dialog.remove(self))
        self.add_suffix(rm)
        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        drag.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value(self.path))
        self.add_controller(drag)
        drop = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
        drop.connect("drop", lambda _t, v, x, y: dialog.drop_on(v, self))
        self.add_controller(drop)

    def problem(self) -> str:
        if self.n <= 0:
            return tr("illisible")
        try:
            return "" if pdftools.parse_ranges(self.pages.get_text(), self.n) else tr("aucune page")
        except ValueError as e:
            return str(e)


class MergeDialog:
    def __init__(self, window):
        self.window = window
        self.dialog, page, self.action = dialog_shell(tr("Fusionner des PDF"), tr("Fusionner"), 680, 640)
        self.group = Adw.PreferencesGroup(title=tr("Ordre des documents"),
                                          description=tr("Glissez les lignes pour changer l'ordre. "
                                                         "Indiquez les pages à garder de chaque fichier."))
        add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"], tooltip_text=tr("Ajouter des PDF…"))
        add.connect("clicked", self._add)
        self.group.set_header_suffix(add)
        self.list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.group.add(self.list)
        page.add(self.group)
        g = Adw.PreferencesGroup(title=tr("Résultat"))
        self.out = _save_as_row(self.dialog, tr("Enregistrer sous"),
                                pdftools.suggest_output(window.path, tr("fusion")))
        g.add(self.out)
        self.summary = Gtk.Label(xalign=0, css_classes=["dim-label"], margin_top=8, wrap=True)
        g.add(self.summary)
        page.add(g)
        self.rows: list = []
        self._append(window.path, window.password)
        self.action.connect("clicked", self._go)
        self.refresh()

    def _append(self, path, password=""):
        row = _MergeRow(self, path, password)
        self.rows.append(row)
        self.list.append(row)

    def add_paths(self, paths):
        bad = []
        for p in paths:
            if not pdftools.is_pdf(p):
                bad.append(os.path.basename(p))
                continue
            try:
                Document.open(p)
            except Exception:
                bad.append(os.path.basename(p))      # protégé ou abîmé : pas fusionnable ici
                continue
            self._append(p)
        self.refresh()
        if bad:
            alert(self.dialog, tr("Fichiers ignorés"), shown("\n".join(bad)) + "\n\n" +
                  tr("Ce ne sont pas des PDF lisibles, ou ils sont protégés par un mot de passe."))

    def _add(self, *_):
        start = os.path.dirname(self.rows[0].path) if self.rows else os.path.expanduser("~")
        choose_files(self.dialog, tr("Ajouter des PDF"), start, self.add_paths, filters=PDF_FILTER)

    def _rebuild(self):
        while (c := self.list.get_first_child()) is not None:
            self.list.remove(c)
        for r in self.rows:
            self.list.append(r)
        self.refresh()

    def move(self, row, step):
        i = self.rows.index(row)
        j = max(0, min(len(self.rows) - 1, i + step))
        if i != j:
            self.rows.insert(j, self.rows.pop(i))
            self._rebuild()

    def remove(self, row):
        self.rows.remove(row)
        self._rebuild()

    def drop_on(self, path: str, target) -> bool:
        src = next((r for r in self.rows if r.path == path), None)
        if src is None or src is target:
            return False
        self.rows.remove(src)
        self.rows.insert(self.rows.index(target), src)
        self._rebuild()
        return True

    def refresh(self):
        problems = [f"{os.path.basename(r.path)} : {r.problem()}" for r in self.rows if r.problem()]
        total = 0
        for r in self.rows:
            try:
                total += len(pdftools.parse_ranges(r.pages.get_text(), r.n))
            except ValueError:
                pass
        if len(self.rows) < 2:
            problems.append(tr("Ajoutez au moins un autre document (+)"))
        self.summary.set_text(shown("\n".join(problems)) if problems else
                              ntr("{0} page au total", "{0} pages au total", total).format(total))
        self.action.set_sensitive(not problems)

    def _go(self, *_):
        items = [(r.path, r.pages.get_text(), r.password) for r in self.rows]
        dst = _out(self.out)
        w = self.window
        self.dialog.close()
        run_job(w, tr("Fusion de {0} PDF").format(len(items)), lambda j: pdftools.merge(j, items, dst),
                lambda job: w.tool_result(job.result))

    def present(self):
        self.dialog.present(self.window)


# ---------------------------------------------------------------------------------------------
# Filigrane
# ---------------------------------------------------------------------------------------------

POSITION_LABELS = [tr("Centre"), tr("Mosaïque (répété)"), tr("En haut à gauche"), tr("En haut à droite"),
                   tr("En bas à gauche"), tr("En bas à droite"), tr("En haut"), tr("En bas")]
WM_COLORS = [(tr("Rouge"), (0.8, 0.0, 0.0)), (tr("Gris"), (0.5, 0.5, 0.5)), (tr("Bleu"), (0.0, 0.2, 0.8)),
             (tr("Noir"), (0.0, 0.0, 0.0))]


class WatermarkDialog:
    def __init__(self, window):
        self.window = window
        path = window.path
        self.dialog, _page, self.action = dialog_shell(tr("Filigrane"), tr("Appliquer"), 820, 720)
        box = Gtk.Box(spacing=12)
        prefs = Adw.PreferencesPage(hexpand=True)
        self.dialog.get_child().set_content(box)
        box.append(prefs)
        self.preview = Gtk.Picture(width_request=280, can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN,
                                   margin_top=18, margin_end=18, margin_bottom=18, valign=Gtk.Align.START)
        box.append(self.preview)
        g = Adw.PreferencesGroup(title=tr("Filigrane"))
        self.kind = combo_row(tr("Type"), [tr("Texte"), tr("Image")])
        g.add(self.kind)
        self.text = Adw.EntryRow(title=tr("Texte"), text=tr("CONFIDENTIEL"))
        g.add(self.text)
        self.image = path_row(tr("Image (PNG, JPEG, SVG…)"), "",
                              lambda r: choose_files(self.dialog, tr("Choisir une image"), os.path.expanduser("~"),
                                                     lambda ps: r.set_text(ps[0]), multiple=False,
                                                     filters=[(tr("Images"), ["*.png", "*.jpg", "*.jpeg", "*.svg",
                                                                              "*.webp", "*.PNG", "*.JPG"])]))
        g.add(self.image)
        self.font = Gtk.FontDialogButton(dialog=Gtk.FontDialog(), valign=Gtk.Align.CENTER,
                                         font_desc=Pango.FontDescription.from_string("Sans Bold 60"),
                                         use_size=False, use_font=True)
        self.font_row = Adw.ActionRow(title=tr("Police"))
        self.font_row.add_suffix(self.font)
        g.add(self.font_row)
        self.size = Adw.SpinRow.new_with_range(6, 400, 2)
        self.size.set_title(tr("Taille (points)"))
        self.size.set_value(60)
        g.add(self.size)
        self.color = combo_row(tr("Couleur"), [c for c, _v in WM_COLORS] + [tr("Personnalisée…")])
        g.add(self.color)
        self.custom = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        rgba = Gdk.RGBA()
        rgba.parse("#0e7c86")
        self.custom.set_rgba(rgba)
        self.color.add_suffix(self.custom)
        prefs.add(g)
        g = Adw.PreferencesGroup(title=tr("Disposition"))
        self.opacity = Adw.SpinRow.new_with_range(5, 100, 5)
        self.opacity.set_title(tr("Opacité (%)"))
        self.opacity.set_value(30)
        g.add(self.opacity)
        self.angle = Adw.SpinRow.new_with_range(-180, 180, 5)
        self.angle.set_title(tr("Angle (degrés)"))
        self.angle.set_value(45)
        g.add(self.angle)
        self.position = combo_row(tr("Position"), POSITION_LABELS)
        g.add(self.position)
        self.pages = Adw.EntryRow(title=tr("Pages (vide = toutes, ex. 1, 3-5)"))
        g.add(self.pages)
        prefs.add(g)
        g = Adw.PreferencesGroup(title=tr("Sécurité"))
        self.flatten = Adw.SwitchRow(title=tr("Aplatir (pages converties en images)"),
                                     subtitle=tr("Le filigrane ne peut plus être retiré ; texte non sélectionnable"))
        g.add(self.flatten)
        self.dpi = Adw.SpinRow.new_with_range(72, 600, 25)
        self.dpi.set_title(tr("Résolution de l'aplatissement (ppp)"))
        self.dpi.set_value(200)
        g.add(self.dpi)
        prefs.add(g)
        g = Adw.PreferencesGroup(title=tr("Résultat"))
        self.out = _save_as_row(self.dialog, tr("Enregistrer sous"), pdftools.suggest_output(path, tr("filigrane")))
        g.add(self.out)
        prefs.add(g)
        for wdg, sig in ((self.kind, "notify::selected"), (self.text, "changed"), (self.image, "changed"),
                         (self.font, "notify::font-desc"), (self.size, "notify::value"),
                         (self.color, "notify::selected"), (self.custom, "notify::rgba"),
                         (self.opacity, "notify::value"), (self.angle, "notify::value"),
                         (self.position, "notify::selected"), (self.flatten, "notify::active")):
            wdg.connect(sig, lambda *_: self._changed())
        self.action.connect("clicked", self._go)
        self._page = None
        self._preview_id = 0
        page_texture(path, 560, window.view.page, window.password, self._set_page)
        self._changed()

    def options(self) -> pdftools.WatermarkOptions:
        img = self.kind.get_selected() == 1
        ci = self.color.get_selected()
        if ci < len(WM_COLORS):
            color = WM_COLORS[ci][1]
        else:
            c = self.custom.get_rgba()
            color = (c.red, c.green, c.blue)
        desc = self.font.get_font_desc()
        font = desc.to_string() if desc else "Sans Bold"
        font = " ".join(p for p in font.split() if not p.replace(".", "").isdigit()) or "Sans Bold"
        return pdftools.WatermarkOptions(
            text="" if img else self.text.get_text(), image=self.image.get_text().strip() if img else "",
            font=font, size=self.size.get_value() if not img else max(5, min(100, self.size.get_value())),
            color=color, opacity=self.opacity.get_value() / 100, angle=self.angle.get_value(),
            position=pdftools.POSITIONS[self.position.get_selected()], pages=self.pages.get_text(),
            flatten=self.flatten.get_active(), dpi=int(self.dpi.get_value()))

    def _changed(self):
        img = self.kind.get_selected() == 1
        self.text.set_visible(not img)
        self.font_row.set_visible(not img)
        self.image.set_visible(img)
        self.size.set_title(tr("Largeur (% de la page)") if img else tr("Taille (points)"))
        self.custom.set_visible(self.color.get_selected() == len(WM_COLORS))
        self.color.set_visible(not img)
        self.dpi.set_sensitive(self.flatten.get_active())
        o = self.options()
        self.action.set_sensitive(bool(o.text.strip()) or (bool(o.image) and os.path.isfile(o.image)))
        if self._preview_id:
            GLib.source_remove(self._preview_id)
        self._preview_id = GLib.timeout_add(150, self._render_preview)

    def _set_page(self, tex, size, raw):
        self._page = (raw, size) if raw is not None else None
        self._render_preview()

    def _render_preview(self):
        self._preview_id = 0
        if self._page is None:
            return False
        import cairo
        (data, w, h, stride), (pw, ph) = self._page
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        base = cairo.ImageSurface.create_for_data(bytearray(data), cairo.FORMAT_ARGB32, w, h, stride)
        ctx = cairo.Context(surf)
        ctx.set_source_surface(base, 0, 0)
        ctx.paint()
        ctx.scale(w / pw, h / ph)
        o = self.options()
        try:
            if o.image and os.path.isfile(o.image):
                pdftools.draw_image(ctx, o, pw, ph, {})
            elif o.text.strip():
                pdftools.draw_text(ctx, o, pw, ph)
        except Exception:
            pass
        surf.flush()
        from ..backend.render import make_texture
        self.preview.set_paintable(make_texture(bytes(surf.get_data()), w, h, surf.get_stride()))
        return False

    def _go(self, *_):
        o = self.options()
        w = self.window
        if o.pages.strip():
            try:
                pdftools.parse_ranges(o.pages, w.document.n_pages)
            except ValueError as e:
                alert(self.dialog, tr("Pages invalides"), shown(str(e)))
                return
        src, pw, dst = w.path, w.password, _out(self.out)
        self.dialog.close()
        run_job(w, tr("Filigrane sur {0}").format(shown(os.path.basename(src))),
                lambda j: pdftools.watermark(j, src, dst, o, pw), lambda job: w.tool_result(job.result))

    def present(self):
        self.dialog.present(self.window)


# ---------------------------------------------------------------------------------------------
# Mot de passe
# ---------------------------------------------------------------------------------------------

class ProtectDialog:
    """Protéger par un mot de passe (AES-256, qpdf), générateur et jauge repris de Pressoir."""

    def __init__(self, window):
        self.window = window
        path = window.path
        self.dialog, page, self.action = dialog_shell(tr("Protéger par un mot de passe"), tr("Protéger"), 560, 640)
        g = Adw.PreferencesGroup(title=tr("Mot de passe d'ouverture"),
                                 description=tr("Chiffrement AES-256. Sans ce mot de passe, le document ne "
                                                "s'ouvre plus : conservez-le en lieu sûr."))
        self.pw = Adw.PasswordEntryRow(title=tr("Mot de passe"))
        gen = Gtk.Button(icon_name="media-playlist-shuffle-symbolic", tooltip_text=tr("Générer un mot de passe fort"),
                         valign=Gtk.Align.CENTER, css_classes=["flat"])
        gen.connect("clicked", self._generate)
        self.pw.add_suffix(gen)
        self.strength = Gtk.LevelBar(min_value=0, max_value=128, valign=Gtk.Align.CENTER, width_request=60,
                                     css_classes=["calypso-strength"])
        self.pw.add_suffix(self.strength)
        self.pw2 = Adw.PasswordEntryRow(title=tr("Confirmer le mot de passe"))
        for r in (self.pw, self.pw2):
            r.connect("changed", lambda *_: self._refresh())
        g.add(self.pw)
        g.add(self.pw2)
        self.generated = Gtk.Label(xalign=0, selectable=True, wrap=True, css_classes=["monospace", "dim-label"],
                                   margin_top=8)
        g.add(self.generated)
        page.add(g)
        g = Adw.PreferencesGroup(title=tr("Droits une fois ouvert"))
        self.print = Adw.SwitchRow(title=tr("Autoriser l'impression"), active=True)
        self.copy = Adw.SwitchRow(title=tr("Autoriser la copie du texte"), active=True)
        self.modify = Adw.SwitchRow(title=tr("Autoriser les modifications"), active=True)
        for r in (self.print, self.copy, self.modify):
            g.add(r)
        page.add(g)
        g = Adw.PreferencesGroup(title=tr("Résultat"))
        self.replace = Adw.SwitchRow(title=tr("Remplacer le document"),
                                     subtitle=tr("Sinon, une copie protégée est créée à côté"))
        g.add(self.replace)
        self.out = _save_as_row(self.dialog, tr("Enregistrer sous"), pdftools.suggest_output(path, tr("protégé")))
        self.replace.bind_property("active", self.out, "sensitive",
                                   GObject.BindingFlags.INVERT_BOOLEAN | GObject.BindingFlags.SYNC_CREATE)
        g.add(self.out)
        page.add(g)
        self.action.connect("clicked", self._go)
        self._refresh()

    def _generate(self, *_):
        pw = pdftools.generate_password(20)
        self.pw.set_text(pw)
        self.pw2.set_text(pw)
        self.generated.set_label(tr("Mot de passe généré : {0}").format(pw))
        self.dialog.get_clipboard().set(pw)

    def _refresh(self):
        pw = self.pw.get_text()
        bits, label = pdftools.password_strength(pw)
        self.strength.set_value(min(bits, 128))
        self.strength.set_tooltip_text(tr("{0} — {1} bits").format(label, f"{bits:.0f}"))
        self.strength.set_visible(bool(pw))
        mismatch = bool(pw and self.pw2.get_text() and pw != self.pw2.get_text())
        (self.pw2.add_css_class if mismatch else self.pw2.remove_css_class)("error")
        self.action.set_sensitive(bool(pw) and pw == self.pw2.get_text())

    def _go(self, *_):
        w = self.window
        src, old, new = w.path, w.password, self.pw.get_text()
        replace = self.replace.get_active()
        dst = src if replace else _out(self.out)
        opts = dict(allow_print=self.print.get_active(), allow_copy=self.copy.get_active(),
                    allow_modify=self.modify.get_active())
        self.dialog.close()

        def done(job):
            if replace:
                w.reopen_after_tool(new, tr("Document protégé par un mot de passe"))
            else:
                w.tool_result(job.result)
        run_job(w, tr("Protection de {0}").format(shown(os.path.basename(src))),
                lambda j: pdftools.protect(j, src, dst, new, password=old, replace=replace, **opts), done)

    def present(self):
        self.dialog.present(self.window)


def unprotect(window):
    """Retirer le mot de passe : question, puis copie sans protection ou document remplacé."""
    from .common import AlertDialog
    d = AlertDialog(tr("Retirer le mot de passe ?"),
                    tr("Le document pourra être ouvert par n'importe qui, sans mot de passe."))
    d.add_response("cancel", tr("Annuler"))
    d.add_response("copy", tr("Créer une copie"))
    d.add_response("replace", tr("Retirer du document"))
    d.set_response_appearance("replace", Adw.ResponseAppearance.DESTRUCTIVE)
    d.set_close_response("cancel")
    d.set_default_response("copy")

    def chosen(_d, r):
        if r not in ("copy", "replace"):
            return
        src, pw = window.path, window.password
        replace = r == "replace"
        dst = src if replace else pdftools.suggest_output(src, tr("sans mot de passe"))

        def done(job):
            if replace:
                window.reopen_after_tool("", tr("Mot de passe retiré"))
            else:
                window.tool_result(job.result)
        run_job(window, tr("Retrait du mot de passe"),
                lambda j: pdftools.unprotect(j, src, dst, pw, replace=replace), done)
    d.connect("response", chosen)
    d.present(window)
