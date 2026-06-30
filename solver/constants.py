# File: constants.py - Constantes globales, chemins, état solveur CP-SAT
# Desc: En français, dans l'architecture emploi du temps, je suis…
# Version 1.0.1
# Copyright 2025 DNAvatar.org - Arnaud Maignan
# Licensed under Apache License 2.0 with Commons Clause.
# See LICENSE_HEADER.txt for full terms.
# Date: June 27, 2025
# Logs:
# - Split from solve_ecoles.py monolith
# - User-facing strings: contrat horaire (not h_contrat)

from __future__ import annotations
import os
from pathlib import Path

# Nombre de workers CP-SAT — env TIMETABLER_WORKERS (0 ou absent = OR-Tools décide seul)
NUM_SEARCH_WORKERS: int = int(os.environ.get("TIMETABLER_WORKERS", "0"))

try:
    from progress import push_progress as _push_progress, SolveProgressCallback as _SolveProgressCallback
except ImportError:
    def _push_progress(_: dict) -> None: pass  # noqa: E704
    class _SolveProgressCallback: pass  # type: ignore[no-redef]

XLSX = Path(__file__).parent.parent.parent / "complexe_lasallien.xlsx"
HTML_OUT = Path(__file__).parent / "planning.html"

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi"]

# Onglets lus comme INPUT — tout autre onglet dans le XLS est ignoré.
# Règle : un onglet ajouté au XLS peut devenir un input (nouvelles contraintes),
# jamais un output. Les résultats vivent dans planning.html, pas dans le XLS.
INPUT_ONGLETS = {
    "etablissements", "distances", "classes", "matieres", "programmes",
    "professeurs", "preferences", "salles", "creneaux",
}

# Poids des pénalités dans l'objectif
POIDS = {"haute": 100, "moyenne": 10, "basse": 1, "contrainte": 1000}
# Minimiser les heures de contrat non couvertes par l'enseignement (autres tâches possibles)
POIDS_CONTRAT_SLACK = 5
# Pré-affectation : pénaliser l'usage des profs Combleur (repli seulement si pass 1 infaisable)
POIDS_COMBLEUR = 10_000
LIBELLE_COMBLEUR = "Combleur fictif"
TOOLTIP_COMBLEUR_ALT0 = "Prof fictif — comble le déficit d'heures"
TOOLTIP_COMBLEUR_ALT2 = (
    "Placeholder de staffing : le programme dépasse la capacité des vrais profs. "
    "Réduire le contrat horaire des Combleurs, ajouter un prof, ou ajuster le programme."
)
CONSEIL_COMBLEUR_AJOUT = (
    "Pour avancer : ajoutez un prof « Combleur fictif » (nom contenant « Combleur »), "
    "avec les matières en tension et un contrat horaire de 18–25 h. "
    "Les Combleurs absorbent le surplus de programme ; le diagnostic les signale en rouge."
)
CONSEIL_COMBLEUR_PLUS = (
    "Pour avancer : augmentez le contrat horaire d’un Combleur existant ou ajoutez un autre prof "
    "Combleur (mêmes matières que le goulet, contrat horaire 18–25 h)."
)
# Meta dernière pré-affectation (diagnostic UI)
PRE_ASSIGN_META: dict = {}

# Solveur CP-SAT en cours — permet l'arrêt externe (/api/stop) depuis un autre thread.
# CP-SAT rend alors la meilleure solution trouvée jusque-là (ou UNKNOWN si aucune).
_ACTIVE_SOLVER = None


def stop_active_solver() -> bool:
    """Demande l'arrêt du CP-SAT en cours. Retourne True si un solveur tournait."""
    s = _ACTIVE_SOLVER
    if s is None:
        return False
    try:
        s.stop_search()
    except AttributeError:
        s.StopSearch()  # ortools < 9.8 (CamelCase)
    return True
