"""Signatures : bandeau et panneau (vérification), « Signer… » (certificat de la base NSS, zone tracée
sur la page), import d'un certificat .p12 / .pfx."""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from ..backend import pdftools, signing  # noqa: E402
from ..i18n import tr  # noqa: E402
from .common import AlertDialog, alert, choose_files, combo_row, dialog_shell, path_row, choose_save, shown  # noqa: E402

STATUS_TEXT = {"valid": tr("Signature valide"), "invalid": tr("Signature invalide"),
               "digest": tr("Document modifié après la signature"), "error": tr("Signature illisible"),
               "not-verified": tr("Signature non vérifiée")}
CERT_TEXT = {"trusted": tr("certificat de confiance"), "untrusted": tr("certificat non reconnu comme de confiance"),
             "expired": tr("certificat expiré"), "revoked": tr("certificat révoqué"),
             "unknown": tr("certificat non vérifié")}


def icon_for(sig) -> str:
    if sig.status != "valid":
        return "dialog-error-symbolic"
    return "security-high-symbolic" if sig.certificate == "trusted" else "dialog-warning-symbolic"


def check_document(window, document):
    """Vérifie les signatures dans un fil ; le bandeau et le panneau suivent."""
    with document.lock:
        n = document.doc.get_n_signatures()
    if not n:
        window.show_signatures([])
        return

    def work():
        try:
            sigs = signing.validate(document)
        except Exception:
            sigs = []
        GLib.idle_add(lambda: (document is window.document and window.show_signatures(sigs), False)[1])
    threading.Thread(target=work, name="calypso-signatures", daemon=True).start()


def signature_row(sig) -> Gtk.Widget:
    row = Gtk.ListBoxRow()
    row._sig = sig
    box = Gtk.Box(spacing=8, margin_top=6, margin_bottom=6)
    box.append(Gtk.Image(icon_name=icon_for(sig), valign=Gtk.Align.START))
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
    texts.append(Gtk.Label(label=shown(sig.signer) or tr("Signataire inconnu"), xalign=0, css_classes=["heading"],
                           ellipsize=Pango.EllipsizeMode.END))
    for line in (STATUS_TEXT.get(sig.status, ""), CERT_TEXT.get(sig.certificate, "") if sig.status == "valid" else "",
                 sig.time, tr("Émis par {0}").format(shown(sig.issuer)) if sig.issuer else ""):
        if line:
            texts.append(Gtk.Label(label=line, xalign=0, wrap=True, css_classes=["dim-label", "caption"]))
    box.append(texts)
    row.set_child(box)
    return row


# ---------------------------------------------------------------------------------------------
# Certificats
# ---------------------------------------------------------------------------------------------

def import_certificate(window, then=None):
    """.p12 / .pfx → mot de passe → import dans la base de certificats."""
    def chosen(paths):
        path = paths[0]
        group = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        entry = Adw.PasswordEntryRow(title=tr("Mot de passe du certificat"))
        group.append(entry)
        d = AlertDialog(tr("Importer « {0} »").format(shown(os.path.basename(path))),
                        tr("Le certificat est ajouté à votre base de certificats ({0}).").format(
                            shown(signing.ensure_nss())))
        d.add_response("cancel", tr("Annuler"))
        d.add_response("import", tr("Importer"))
        d.set_response_appearance("import", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("import")
        d.set_close_response("cancel")
        d.set_extra_child(group)
        entry.connect("entry-activated", lambda *_: (d.response("import"), d.close()))

        def done(_d, r):
            if r != "import":
                return
            err = signing.import_p12(path, entry.get_text())
            if err:
                alert(window, tr("Import impossible"), shown(err))
                return
            from .common import toast
            toast(window, tr("Certificat importé"))
            if then is not None:
                then()
        d.connect("response", done)
        d.present(window)
    choose_files(window, tr("Importer un certificat"), os.path.expanduser("~"), chosen, multiple=False,
                 filters=[(tr("Certificats (.p12, .pfx)"), ["*.p12", "*.pfx", "*.P12", "*.PFX"])])


def start_signing(window):
    """« Signer… » : sans certificat, proposer d'en importer un ; sinon tracer la zone."""
    if not signing.certificates():
        d = AlertDialog(tr("Aucun certificat de signature"),
                        tr("Pour signer, il faut un certificat personnel (fichier .p12 ou .pfx, fourni par votre "
                           "administration, votre entreprise ou une autorité de certification). Les certificats "
                           "installés dans Firefox sont aussi utilisés."))
        d.add_response("cancel", tr("Annuler"))
        d.add_response("import", tr("Importer un certificat…"))
        d.set_response_appearance("import", Adw.ResponseAppearance.SUGGESTED)
        d.set_close_response("cancel")
        d.connect("response", lambda _d, r: r == "import" and import_certificate(window, lambda: start_signing(window)))
        d.present(window)
        return
    window.begin_sign_area()


class SignDialog:
    def __init__(self, window, page: int, rect):
        self.window = window
        self.page, self.rect = page, rect
        self.certs = signing.certificates()
        self.dialog, page_w, action = dialog_shell(tr("Signer le document"), tr("Signer"), 560, 560)
        g = Adw.PreferencesGroup(title=tr("Certificat"))
        labels = [f"{c.name}" + (f" — {c.organization}" if c.organization else "") for c in self.certs]
        self.cert = combo_row(tr("Signer en tant que"), labels or [tr("Aucun")])
        g.add(self.cert)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=tr("Importer un certificat…"), css_classes=["flat"])
        add.connect("clicked", lambda *_: (self.dialog.close(), import_certificate(window, lambda: SignDialog(
            window, page, rect).present())))
        g.set_header_suffix(add)
        self.password = Adw.PasswordEntryRow(title=tr("Mot de passe de la clé (si elle en a un)"))
        g.add(self.password)
        page_w.add(g)
        g = Adw.PreferencesGroup(title=tr("Signature visible"))
        name = self.certs[0].name if self.certs else ""
        self.text = Adw.EntryRow(title=tr("Texte"), text=tr("Signé par {0}").format(name))
        g.add(self.text)
        self.reason = Adw.EntryRow(title=tr("Motif (facultatif)"))
        g.add(self.reason)
        self.location = Adw.EntryRow(title=tr("Lieu (facultatif)"))
        g.add(self.location)
        page_w.add(g)
        g = Adw.PreferencesGroup(title=tr("Résultat"), description=tr("Une copie signée est créée ; le document "
                                                                      "d'origine reste tel quel."))
        self.out = path_row(tr("Enregistrer sous"), pdftools.suggest_output(window.path, tr("signé")),
                            lambda r: choose_save(self.dialog, tr("Enregistrer le PDF"), r.get_text(), r.set_text),
                            icon="document-save-symbolic")
        g.add(self.out)
        page_w.add(g)
        self.cert.connect("notify::selected", self._cert_changed)
        action.set_sensitive(bool(self.certs))
        action.connect("clicked", self._go)

    def _cert_changed(self, *_):
        i = self.cert.get_selected()
        if 0 <= i < len(self.certs):
            self.text.set_text(tr("Signé par {0}").format(self.certs[i].name))

    def _go(self, *_):
        w = self.window
        cert = self.certs[self.cert.get_selected()]
        dst = self.out.get_text().strip()
        if not dst.lower().endswith(".pdf"):
            dst += ".pdf"
        if os.path.lexists(dst):
            dst = os.path.join(os.path.dirname(dst), pdftools.unique_name(os.path.dirname(dst), os.path.basename(dst)))
        self.dialog.close()

        def done(err):
            if err:
                low = err.lower()
                if "password" in low or "pin" in low:
                    err = tr("Mot de passe de la clé incorrect ou manquant.") + "\n" + err
                alert(w, tr("La signature a échoué"), shown(err))
            else:
                w.tool_result(dst, tr("signé par {0}").format(shown(cert.name)))
        signing.sign(w.document, cert, self.page, self.rect, dst, self.text.get_text(), self.reason.get_text(),
                     self.location.get_text(), self.password.get_text(), done)

    def present(self):
        self.dialog.present(self.window)
