# File: solve_ecoles.py - Façade publique du solveur emploi du temps
# Desc: Point d'entrée inchangé pour server.py et make_demo_cache.py
# Version 2.0.0
# Copyright 2025 DNAvatar.org - Arnaud Maignan
# Licensed under Apache License 2.0 with Commons Clause.
# See LICENSE_HEADER.txt for full terms.
# Date: June 27, 2025
# Logs:
# - Split into solver/ package; this file re-exports the public API

"""
Optimisation emploi du temps — OR-Tools CP-SAT.
Modules : solver/charger.py, diagnostic.py, pre_assign.py, resoudre.py, …

Usage :
    uv run python solve_ecoles.py
"""

from solver.affichage import (
    afficher_emploi_du_temps,
    afficher_emploi_du_temps_profs,
    afficher_resume,
    afficher_satisfaction_preferences,
)
from solver.charger import charger_donnees, charger_donnees_dict
from solver.combleur import (
    conseil_combleur_pour_erreur,
    conseil_combleur_staffing,
)
from solver.constants import (
    CONSEIL_COMBLEUR_AJOUT,
    CONSEIL_COMBLEUR_PLUS,
    HTML_OUT,
    INPUT_ONGLETS,
    JOURS,
    LIBELLE_COMBLEUR,
    POIDS,
    POIDS_COMBLEUR,
    POIDS_CONTRAT_SLACK,
    PRE_ASSIGN_META,
    TOOLTIP_COMBLEUR_ALT0,
    TOOLTIP_COMBLEUR_ALT2,
    XLSX,
    stop_active_solver,
)
from solver.diagnostic import (
    diagnostiquer_charge_profs,
    enrichir_diagnostic_matiere,
    heures_par_prof,
    serialiser_diagnostic,
)
from solver.diagnostic_core import diagnostiquer
from solver.donnees import (
    Classe,
    Creneau,
    DistanceEtab,
    DonneesCollege,
    Etablissement,
    Matiere,
    Preference,
    Prof,
    ProgrammeItem,
    Salle,
)
from solver.pre_assign import pre_assigner_profs
from solver.programme import matiere_applicable, nb_creneaux
from solver.resoudre import resoudre
from solver.salles import capacite_par_etab_type, capacite_par_type

__all__ = [
    "CONSEIL_COMBLEUR_AJOUT",
    "CONSEIL_COMBLEUR_PLUS",
    "Classe",
    "Creneau",
    "DistanceEtab",
    "DonneesCollege",
    "Etablissement",
    "HTML_OUT",
    "INPUT_ONGLETS",
    "JOURS",
    "LIBELLE_COMBLEUR",
    "Matiere",
    "POIDS",
    "POIDS_COMBLEUR",
    "POIDS_CONTRAT_SLACK",
    "PRE_ASSIGN_META",
    "Preference",
    "Prof",
    "ProgrammeItem",
    "Salle",
    "TOOLTIP_COMBLEUR_ALT0",
    "TOOLTIP_COMBLEUR_ALT2",
    "XLSX",
    "afficher_emploi_du_temps",
    "afficher_emploi_du_temps_profs",
    "afficher_resume",
    "afficher_satisfaction_preferences",
    "capacite_par_etab_type",
    "capacite_par_type",
    "charger_donnees",
    "charger_donnees_dict",
    "conseil_combleur_pour_erreur",
    "conseil_combleur_staffing",
    "diagnostiquer",
    "diagnostiquer_charge_profs",
    "enrichir_diagnostic_matiere",
    "heures_par_prof",
    "matiere_applicable",
    "nb_creneaux",
    "pre_assigner_profs",
    "resoudre",
    "serialiser_diagnostic",
    "stop_active_solver",
]

if __name__ == "__main__":
    print("\n=== CHARGEMENT DES DONNÉES ===")
    d = charger_donnees()
    afficher_resume(d)

    print("\n=== CONSTRUCTION DU MODÈLE ===")
    result = resoudre(d)

    if result is not None:
        solution, score, temps, propagations, statut = result
        afficher_emploi_du_temps(solution, d)
        afficher_emploi_du_temps_profs(solution, d)
        afficher_satisfaction_preferences(solution, d)

        print("\n=== EXPORT HTML ===")
        from export_html import generer_html  # noqa: E402
        generer_html(solution, d, HTML_OUT, score=score, temps=temps)
