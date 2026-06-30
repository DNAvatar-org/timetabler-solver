# File: staffing_bilan.py - Messages bilan staffing et écarts contrats
# Desc: En français, dans l'architecture emploi du temps, je suis…
# Version 1.0.1
# Copyright 2025 DNAvatar.org - Arnaud Maignan
# Licensed under Apache License 2.0 with Commons Clause.
# See LICENSE_HEADER.txt for full terms.
# Date: June 27, 2025
# Logs:
# - Split from solve_ecoles.py monolith
# - Error messages: contrat horaire (not h_contrat)

from __future__ import annotations
import math

from solver.diagnostic_core import diagnostiquer
from solver.donnees import DonneesCollege

def _bloc_bilan_staffing(
    d: DonneesCollege,
    charge: dict[str, int],
) -> str:
    """
    Bilan cohérent programme / contrats / matières.
    N'additionne pas les « surplus » par matière (double comptage des profs polyvalents).
    """
    lignes_diag = diagnostiquer(d)
    besoin_prog = sum(l["besoin_h"] for l in lignes_diag)
    total_contrats = sum(p.h_contrat for p in d.profs.values())
    total_assigne = sum(charge.values())

    lignes = [
        f"\nBilan staffing — {len(d.classes)} classes, {besoin_prog}h programme/semaine :",
        f"  • Heures de cours à placer : {besoin_prog}h",
        f"  • Contrats horaires profs (total) : {total_contrats}h ({len(d.profs)} profs)",
    ]
    if total_assigne and total_assigne != total_contrats:
        lignes.append(f"  • Heures réellement assignées : {total_assigne}h")

    ecart = total_contrats - besoin_prog
    if ecart > 0:
        lignes.append(
            f"  • Écart global : +{ecart}h de contrats horaires au-delà du programme"
            f" (~{ecart / 18:.1f} ETP fictifs)"
        )
        lignes.append(
            "    Un prof MATH+PC compte dans les deux matières : ne pas sommer les surplus par matière."
        )
    elif ecart < 0:
        lignes.append(
            f"  • Écart global : {ecart}h — sous-effectif, augmenter un contrat horaire."
        )

    lignes.append("\nBesoins par matière (ETP min = ⌈besoin ÷ 18⌉, profs = pool éligible) :")
    surstaff = []
    for l in lignes_diag:
        besoin = l["besoin_h"]
        etp_min = math.ceil(besoin / 18) if besoin else 0
        nb_profs = len(l["profs"])
        partage = any(p["partage"] for p in l["profs"])
        note = ""
        if nb_profs > etp_min + 1:
            note = f" ← pool large (+{nb_profs - etp_min} prof(s) vs ETP min)"
            surstaff.append(l["matiere_id"])
        elif nb_profs > etp_min and partage:
            note = " ← profs partagés entre matières"
        lignes.append(
            f"  • {l['matiere_id']:<5} {besoin:3}h | {nb_profs} prof(s) | {etp_min} ETP min{note}"
        )

    if surstaff:
        lignes.append(
            f"\n  Pools surdimensionnés : {', '.join(surstaff)}"
            " — réduire le contrat horaire ou retirer un prof du pool si l'écart global est positif."
        )

    return "\n".join(lignes)


def _message_ecarts_contrats(
    charge: dict[str, int],
    d: DonneesCollege,
) -> str | None:
    """Formate les écarts contrat/effectif avec un total agrégé."""
    ecarts: list[str] = []
    total_assigne = 0
    total_contrat = 0
    manque = 0
    surplus = 0

    for p_id in sorted(charge.keys()):
        h = charge[p_id]
        prof = d.profs[p_id]
        total_assigne += h
        total_contrat += prof.h_contrat
        if h == prof.h_contrat:
            continue
        sens = "surchargé" if h > prof.h_contrat else "sous-chargé"
        delta = abs(h - prof.h_contrat)
        ecarts.append(
            f"  • {prof.nom_complet} : {h}h assignées / {prof.h_contrat}h contrat horaire ({sens}, Δ{delta}h)"
        )
        if h < prof.h_contrat:
            manque += prof.h_contrat - h
        else:
            surplus += h - prof.h_contrat

    if not ecarts:
        return None

    ligne_tot = f"\n→ Total : {total_assigne}h assignées / {total_contrat}h contrats horaires"
    if manque:
        ligne_tot += f" — {manque}h de contrat horaire non couvertes"
    if surplus:
        ligne_tot += f" — {surplus}h au-delà des contrats horaires"

    bloc_mat = _bloc_bilan_staffing(d, charge)

    return (
        "Contrats non respectés — staffing à corriger :\n"
        + "\n".join(ecarts)
        + ligne_tot
        + bloc_mat
        + "\n\nAjustez le contrat horaire pour coller au volume réellement assignable, "
        "ou le Diagnostic matière par matière."
    )
