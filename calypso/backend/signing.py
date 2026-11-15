"""Signatures numériques : vérifier celles d'un document, signer avec un certificat (Poppler + NSS).

Base de certificats NSS (celle de Firefox, comme Papers et Evince) : CALYPSO_NSS_DIR si posé (tests),
sinon le profil Firefox (classique ou snap), sinon ~/.pki/nssdb (créé vide au besoin, certutil).
Elle doit être choisie AVANT la première opération NSS du processus (`ensure_nss`). Un certificat
reçu en .p12 / .pfx (impôts, ANTS, entreprise…) s'importe dans cette base (pk12util).
Vérification sans requête réseau (pas d'OCSP) : rapide, et rien ne part sur Internet.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Optional

import gi

gi.require_version("Poppler", "0.18")
from gi.repository import GLib, Poppler  # noqa: E402

from ..i18n import tr  # noqa: E402

_nss_lock = threading.Lock()
_nss_dir: Optional[str] = None


def _firefox_db() -> str:
    home = os.path.expanduser("~")
    for pattern in (".mozilla/firefox/*.default-release", ".mozilla/firefox/*.default*",
                    "snap/firefox/common/.mozilla/firefox/*.default*"):
        for d in sorted(glob.glob(os.path.join(home, pattern))):
            if os.path.exists(os.path.join(d, "cert9.db")):
                return d
    return ""


def ensure_nss() -> str:
    """Choisit la base de certificats (une seule fois par processus) et la renvoie."""
    global _nss_dir
    with _nss_lock:
        if _nss_dir is not None:
            return _nss_dir
        d = os.environ.get("CALYPSO_NSS_DIR") or _firefox_db()
        if not d:
            d = os.path.expanduser("~/.pki/nssdb")
            if not os.path.exists(os.path.join(d, "cert9.db")) and shutil.which("certutil"):
                os.makedirs(d, mode=0o700, exist_ok=True)
                subprocess.run(["certutil", "-N", "-d", "sql:" + d, "--empty-password"],
                               stdin=subprocess.DEVNULL, capture_output=True)
        Poppler.set_nss_dir(d)
        _nss_dir = d
        return d


@dataclass
class Certificate:
    id: str
    name: str
    email: str
    organization: str
    issuer: str
    expires: str
    info: object


def _when(dt) -> str:
    try:
        return dt.to_local().format("%x %X") if dt is not None else ""
    except Exception:
        return ""


def certificates() -> list:
    ensure_nss()
    out = []
    for c in Poppler.get_available_signing_certificates() or []:
        out.append(Certificate(c.get_id() or "", c.get_subject_common_name() or "", c.get_subject_email() or "",
                               c.get_subject_organization() or "", c.get_issuer_common_name() or "",
                               _when(c.get_expiration_time()), c))
    return out


def import_p12(path: str, password: str) -> str:
    """Importe un certificat .p12 / .pfx dans la base ; renvoie le message d'erreur ('' : réussi)."""
    d = ensure_nss()
    if not shutil.which("pk12util"):
        return tr("pk12util est introuvable (sudo apt install libnss3-tools)")
    r = subprocess.run(["pk12util", "-i", path, "-d", "sql:" + d, "-W", password], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        low = (r.stdout + r.stderr).lower()
        if "password" in low or "pkcs12 decode" in low or "integrity" in low:
            return tr("Mot de passe du certificat incorrect.")
        lines = [ln for ln in (r.stderr or r.stdout).splitlines() if ln.strip()]
        return lines[-1] if lines else tr("Import impossible")
    return ""


@dataclass
class SignatureInfo:
    field: str
    signer: str
    status: str                 # valid, invalid, digest, not-verified, error
    certificate: str            # trusted, untrusted, expired, revoked, unknown
    time: str
    issuer: str
    page: int = -1
    rect: tuple = ()


SIG_STATUS = {Poppler.SignatureStatus.VALID: "valid", Poppler.SignatureStatus.INVALID: "invalid",
              Poppler.SignatureStatus.DIGEST_MISMATCH: "digest", Poppler.SignatureStatus.DECODING_ERROR: "error",
              Poppler.SignatureStatus.GENERIC_ERROR: "error", Poppler.SignatureStatus.NOT_FOUND: "error",
              Poppler.SignatureStatus.NOT_VERIFIED: "not-verified"}
CERT_STATUS = {Poppler.CertificateStatus.TRUSTED: "trusted", Poppler.CertificateStatus.UNTRUSTED_ISSUER: "untrusted",
               Poppler.CertificateStatus.UNKNOWN_ISSUER: "untrusted", Poppler.CertificateStatus.REVOKED: "revoked",
               Poppler.CertificateStatus.EXPIRED: "expired", Poppler.CertificateStatus.GENERIC_ERROR: "unknown",
               Poppler.CertificateStatus.NOT_VERIFIED: "unknown"}


def validate(document) -> list:
    """Signatures du document, vérifiées (à appeler dans un fil : peut prendre un moment)."""
    ensure_nss()
    with document.lock:
        n = document.doc.get_n_signatures()
        fields = document.doc.get_signature_fields() if n else []
    places = {}
    for i in range(document.n_pages):
        for f in document.fields(i):
            if f.kind == "signature":
                places[f.name] = (i, f.rect)
    out = []
    flags = Poppler.SignatureValidationFlags.VALIDATE_CERTIFICATE | \
        Poppler.SignatureValidationFlags.WITHOUT_OCSP_REVOCATION_CHECK
    for f in fields or []:
        with document.lock:
            name = f.get_name() or ""
            try:
                si = f.signature_validate_sync(flags, None)
            except GLib.Error:
                si = None
        if si is None:
            out.append(SignatureInfo(name, "", "error", "unknown", "", ""))
            continue
        ci = si.get_certificate_info()
        page, rect = places.get(name, (-1, ()))
        out.append(SignatureInfo(name, si.get_signer_name() or (ci.get_subject_common_name() if ci else "") or "",
                                 SIG_STATUS.get(si.get_signature_status(), "error"),
                                 CERT_STATUS.get(si.get_certificate_status(), "unknown"),
                                 _when(si.get_local_signing_time()),
                                 (ci.get_issuer_common_name() if ci else "") or "", page, rect))
    return out


def summary(sigs: list) -> tuple[str, str]:
    """(gravité, texte) pour le bandeau : ok, warning, error."""
    if not sigs:
        return "", ""
    bad = [s for s in sigs if s.status in ("invalid", "digest", "error")]
    if bad:
        return "error", tr("Signature invalide : le document a été modifié après sa signature, ou la signature "
                           "est abîmée")
    names = ", ".join(sorted({s.signer for s in sigs if s.signer})) or tr("inconnu")
    if all(s.certificate == "trusted" for s in sigs):
        return "ok", tr("Signé par {0} — signature valide").format(names)
    return "warning", tr("Signé par {0} — signature intacte, certificat non reconnu comme de confiance").format(names)


def sign(document, cert: Certificate, page: int, rect, dst: str, text: str, reason: str = "", location: str = "",
         password: str = "", done=None):
    """Signe le document (copie signée dans dst). rect en points, origine en haut. done(erreur ou '')."""
    ensure_nss()
    h = document.sizes[page][1]
    x, y, w, hh = rect
    sd = Poppler.SigningData()
    sd.set_certificate_info(cert.info)
    sd.set_page(page)
    r = Poppler.Rectangle()
    r.x1, r.y1, r.x2, r.y2 = x, h - y - hh, x + w, h - y
    sd.set_signature_rectangle(r)
    # Poppler 24.02 plante (« basic_string: construction from null ») si une de ces chaînes manque
    sd.set_signature_text(text or "")
    sd.set_signature_text_left(cert.name or "")
    sd.set_reason(reason or "")
    sd.set_location(location or "")
    sd.set_field_partial_name("Signature" + GLib.uuid_string_random()[:8])
    sd.set_password(password or "")
    sd.set_document_owner_password(document.password or "")
    sd.set_document_user_password(document.password or "")
    sd.set_destination_filename(dst)

    def finished(doc, res):
        try:
            ok = doc.sign_finish(res)
            err = "" if ok else tr("La signature a échoué")
        except GLib.Error as e:
            err = e.message or tr("La signature a échoué")
        if done is not None:
            done(err)
    # document à part, relu du fichier : Poppler signe dans son propre fil, sans notre verrou (le
    # fil de rendu lit le document affiché en même temps) ; les annotations ont été enregistrées avant
    fresh = Poppler.Document.new_from_file(GLib.filename_to_uri(os.path.abspath(document.path), None),
                                           document.password or None)
    sign._keep = (fresh, sd)                 # gardés vivants jusqu'à la fin (sinon « No signature given »)
    fresh.sign(sd, None, finished)
