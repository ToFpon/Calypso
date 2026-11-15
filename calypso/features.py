"""Fonctionnalités facultatives de Calypso, toutes désactivables sans redémarrage.

Chaque fonctionnalité se consulte au moment de l'usage (enabled(id)), jamais à l'import :
Préférences → Fonctionnalités l'active ou la coupe tout de suite.
"""

from __future__ import annotations

from dataclasses import dataclass

from .i18n import tr
from .settings import settings


@dataclass(frozen=True)
class Feature:
    id: str
    title: str
    description: str
    icon: str
    default: bool = True


FEATURES = [
    Feature("resume", tr("Reprendre où on s'était arrêté"),
            tr("Un document rouvert revient à la page, au zoom et à la disposition qu'il avait"),
            "document-open-recent-symbolic"),
    Feature("link_preview", tr("Aperçu des liens"),
            tr("Le survol d'un lien vers une autre page du document montre cette page dans une bulle"),
            "find-location-symbolic"),
    Feature("auto_reload", tr("Recharger un document modifié"),
            tr("Un document changé par un autre programme est relu tout seul, à la même page"),
            "view-refresh-symbolic"),
]

BY_ID = {f.id: f for f in FEATURES}


def enabled(fid: str) -> bool:
    f = BY_ID.get(fid)
    if f is None:
        return False
    value = settings["features"].get(fid, f.default)
    return value if isinstance(value, bool) else f.default     # « "no" » écrit à la main ne vaut pas vrai


def set_enabled(fid: str, value: bool):
    feats = dict(settings["features"])
    feats[fid] = bool(value)
    settings["features"] = feats
