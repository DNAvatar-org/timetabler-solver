# File: programme.py - Programme scolaire : niveaux, options, créneaux horaires
# Desc: En français, dans l'architecture emploi du temps, je suis…
# Version 1.0.0
# Copyright 2025 DNAvatar.org - Arnaud Maignan
# Licensed under Apache License 2.0 with Commons Clause.
# See LICENSE_HEADER.txt for full terms.
# Date: June 27, 2025
# Logs:
# - Split from solve_ecoles.py monolith

from __future__ import annotations
import math

from solver.donnees import Classe, Matiere, ProgrammeItem

def nb_creneaux(h_semaine: float) -> int:
    """Convertit des heures hebdomadaires en nombre de créneaux de 1h."""
    return max(1, math.floor(h_semaine + 0.5))


def _split_option_header(col_name: str, known_niveaux: set[str]) -> tuple[str, str]:
    """Sépare un en-tête de colonne option en (niveau, clé_option).

    'sciences'        -> ('',     'sciences')   → toutes classes "…sciences…"
    '1ère sciences'   -> ('1ère', 'sciences')   → seulement les 1ère "…sciences…"
    Le 1er mot reconnu comme niveau de classe est extrait ; le reste = clé option.
    """
    niveau = ""
    reste: list[str] = []
    for mot in str(col_name).split():
        if not niveau and mot in known_niveaux:
            niveau = mot
        else:
            reste.append(mot)
    return niveau, (" ".join(reste) if reste else str(col_name))


def _niveau_match(prog: ProgrammeItem, classe: Classe) -> bool:
    """Vérifie si un ProgrammeItem s'applique à cette classe (niveau ou specificite).

    Pour un item de spécificité (option), le niveau est facultatif :
    - prog.niveau == ""   → l'option s'applique à toutes les classes la portant
    - prog.niveau renseigné → l'option ne vaut que pour ce niveau (ex. "1ère sciences")
    """
    if prog.specificite_requis is not None:
        spec = classe.specificite or ""
        if prog.specificite_requis not in spec:
            return False
        return (not prog.niveau) or prog.niveau == classe.niveau
    return prog.niveau == classe.niveau


# Matières atelier (matieres.salle_speciale) → mot-clé dans classes.specificite
_ATELIER_FILIERE: dict[str, str] = {
    "atelier-aeronautique": "aéronautique",
    "atelier-maintenance": "maintenance",
    "atelier-electricite": "électricité",
}


def matiere_applicable(
    prog: ProgrammeItem,
    classe: Classe,
    matieres: dict[str, Matiere] | None = None,
) -> bool:
    """
    Détermine si une matière du programme s'applique à cette classe.

    Règles LV2 (chaque classe prend soit ESP soit ALL, pas les deux) :
    - ALL → uniquement classes bi-langue
    - ESP en 5e/4e/3e/2nde → uniquement classes non-bi-langue
    Matières atelier (AERO, MAINT, ELEC) : colonnes « 2nde Pro » etc. sont partagées
    entre filières — filtrer via salle_speciale ↔ classes.specificite.
    Les items specificite_requis ignorent ces règles (déjà ciblés par classe).
    """
    if prog.specificite_requis is not None:
        return True  # la sélection par classe est faite dans _niveau_match

    spec = classe.specificite or ""
    est_bilingue = "bi-langue" in spec

    if prog.matiere_id == "ALL":
        return est_bilingue

    if prog.matiere_id == "ESP" and prog.niveau != "6e":
        return not est_bilingue

    if matieres is not None and prog.matiere_id in matieres:
        salle = matieres[prog.matiere_id].salle_type
        if salle in _ATELIER_FILIERE:
            return _ATELIER_FILIERE[salle] in spec.lower()

    return True

