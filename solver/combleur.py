# File: combleur.py - Profs Combleur fictifs — détection et conseils staffing
# Desc: En français, dans l'architecture emploi du temps, je suis…
# Version 1.0.0
# Copyright 2025 DNAvatar.org - Arnaud Maignan
# Licensed under Apache License 2.0 with Commons Clause.
# See LICENSE_HEADER.txt for full terms.
# Date: June 27, 2025
# Logs:
# - Split from solve_ecoles.py monolith

from __future__ import annotations
import re

from solver.constants import (
    CONSEIL_COMBLEUR_AJOUT,
    CONSEIL_COMBLEUR_PLUS,
    LIBELLE_COMBLEUR,
)
from solver.donnees import DonneesCollege, Prof

def _est_combleur(prof: Prof) -> bool:
    """Prof fictif de padding — nom « Combleur… » ou statut combleur (futur)."""
    return "combleur" in prof.nom_complet.lower()


def conseil_combleur_staffing(
    d: DonneesCollege,
    heures_manquantes: int | None = None,
) -> str:
    """Message utilisateur quand la pré-affectation / staffing échoue."""
    nb_c = sum(1 for p in d.profs.values() if _est_combleur(p))
    suffix = ""
    if heures_manquantes and heures_manquantes > 0:
        suffix = f" Manque estimé : ~{heures_manquantes} h."
    if nb_c == 0:
        return CONSEIL_COMBLEUR_AJOUT + suffix
    return CONSEIL_COMBLEUR_PLUS + suffix


_STAFFING_ERR_KEYS = (
    "Répartition impossible",
    "non assignable",
    "Pré-affectation impossible",
    "Surcharge prof",
    "Emploi du temps impossible",
)


def conseil_combleur_pour_erreur(d: DonneesCollege, message: str) -> str | None:
    """Conseil Combleur si le message d'erreur relève du staffing."""
    if not any(k in message for k in _STAFFING_ERR_KEYS):
        return None
    hm = None
    if "~" in message and "h réparties" in message:
        import re
        m = re.search(r"~(\d+)h réparties", message)
        if m:
            hm = int(m.group(1))
    return conseil_combleur_staffing(d, hm)
