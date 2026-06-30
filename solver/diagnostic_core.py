# File: diagnostic_core.py - Diagnostic matière par matière (capacité vs besoin)
# Desc: En français, dans l'architecture emploi du temps, je suis…
# Version 1.0.0
# Copyright 2025 DNAvatar.org - Arnaud Maignan
# Licensed under Apache License 2.0 with Commons Clause.
# See LICENSE_HEADER.txt for full terms.
# Date: June 27, 2025
# Logs:
# - Split from solve_ecoles.py monolith

from __future__ import annotations
from collections import defaultdict

from solver.donnees import DonneesCollege, Prof
from solver.programme import _niveau_match, matiere_applicable, nb_creneaux

def diagnostiquer(d: DonneesCollege) -> list[dict]:
    """
    Vérifie la compatibilité heures-programme vs capacité-profs, matière par matière.
    Retourne une liste de dicts prête pour sérialisation JSON.
    """
    # Heures nécessaires par matière (créneaux entiers, toutes classes confondues)
    besoins: dict[str, int] = defaultdict(int)
    for classe in d.classes:
        for prog in d.programme:
            if not _niveau_match(prog, classe):
                continue
            if prog.matiere_id not in d.matieres:
                continue
            if not matiere_applicable(prog, classe, d.matieres):
                continue
            besoins[prog.matiere_id] += nb_creneaux(prog.h_semaine)

    # Profs par matière
    profs_par_mat: dict[str, list[Prof]] = defaultdict(list)
    for p in d.profs.values():
        for m in p.matieres:
            if m in besoins:
                profs_par_mat[m].append(p)

    lignes = []
    for mat_id in sorted(besoins):
        besoin = besoins[mat_id]
        profs  = sorted(profs_par_mat.get(mat_id, []), key=lambda p: p.id)
        cap    = sum(p.h_contrat for p in profs)
        deficit = besoin - cap
        mat_nom = d.matieres[mat_id].nom if mat_id in d.matieres else mat_id

        lignes.append({
            "matiere_id":   mat_id,
            "matiere_nom":  mat_nom,
            "besoin_h":     besoin,
            "capacite_h":   cap,
            "deficit_h":    deficit,           # négatif = surplus, positif = manque
            "profs": [
                {
                    "id":        p.id,
                    "nom":       p.nom_complet,
                    "h_contrat": p.h_contrat,
                    "partage":   [m for m in p.matieres if m != mat_id],
                }
                for p in profs
            ],
        })

    return lignes
