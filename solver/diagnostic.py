# File: diagnostic.py - Diagnostic JSON API (charge profs, sérialisation)
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

from solver.combleur import _est_combleur, conseil_combleur_pour_erreur
from solver.constants import LIBELLE_COMBLEUR, PRE_ASSIGN_META
from solver.diagnostic_core import diagnostiquer
from solver.donnees import DonneesCollege
from solver.pre_assign import pre_assigner_profs
from solver.programme import _niveau_match, matiere_applicable, nb_creneaux

def enrichir_diagnostic_matiere(
    lignes: list[dict],
    charge: dict,
    d: DonneesCollege,
) -> list[dict]:
    """Enrichit le diagnostic matière : combleurs visibles, h assignées, capacités réelles."""
    charge_map = {
        p["id"]: p["h_assignees"]
        for p in charge.get("profs") or []
    }
    for l in lignes:
        cap_reelle = 0
        cap_combleur = 0
        combleurs_assignes = 0
        enriched: list[dict] = []
        for p in l["profs"]:
            prof = d.profs[p["id"]]
            is_c = _est_combleur(prof)
            h_a = charge_map.get(p["id"], 0)
            ep = {
                **p,
                "combleur":      is_c,
                "h_assignees":   h_a,
                "nom_affichage": LIBELLE_COMBLEUR if is_c else p["nom"],
            }
            if is_c:
                cap_combleur += p["h_contrat"]
                if h_a > 0:
                    combleurs_assignes += 1
            else:
                cap_reelle += p["h_contrat"]
            enriched.append(ep)
        enriched.sort(
            key=lambda x: (
                0 if x["combleur"] else 1,
                -x["h_assignees"],
                x["nom_affichage"],
            ),
        )
        l["profs"] = enriched
        l["capacite_reelle_h"] = cap_reelle
        l["capacite_combleur_h"] = cap_combleur
        l["combleurs_assignes"] = combleurs_assignes
    return lignes


def serialiser_diagnostic(d: DonneesCollege) -> dict:
    """Diagnostic complet JSON (matières enrichies + charge profs + meta pré-affectation)."""
    charge = diagnostiquer_charge_profs(d)
    lignes = enrichir_diagnostic_matiere(diagnostiquer(d), charge, d)
    nb_pb = sum(1 for l in lignes if l["deficit_h"] > 0)
    nb_combleurs_actifs = sum(
        1 for p in charge.get("profs") or []
        if p.get("combleur") and p.get("h_assignees", 0) > 0
    )
    return {
        "ok": (
            nb_pb == 0
            and charge["nb_ecarts"] == 0
            and not charge.get("erreur")
            and nb_combleurs_actifs == 0
        ),
        "nb_conflits": nb_pb,
        "lignes": lignes,
        "charge_profs": charge,
        "pre_assign_sans_combleur": PRE_ASSIGN_META.get("sans_combleur_ok", False),
        "nb_combleurs_actifs": nb_combleurs_actifs,
        "conseil_combleur": charge.get("conseil_combleur"),
    }


def diagnostiquer_charge_profs(d: DonneesCollege) -> dict:
    """
    Pré-affectation greedy : heures assignées vs contrat par prof.
    Retourne un dict prêt pour sérialisation JSON.
    """
    total_contrat = sum(p.h_contrat for p in d.profs.values())

    try:
        affectation = pre_assigner_profs(d)
    except ValueError as e:
        msg = str(e)
        conseil = conseil_combleur_pour_erreur(d, msg)
        return {
            "profs":           [],
            "erreur":          msg,
            "conseil_combleur": conseil,
            "total_assigne":   0,
            "total_contrat":   total_contrat,
            "nb_ecarts":       0,
        }

    charge: dict[str, int] = defaultdict(int)
    for classe in d.classes:
        for prog in d.programme:
            if not _niveau_match(prog, classe):
                continue
            if prog.matiere_id not in d.matieres:
                continue
            if not matiere_applicable(prog, classe, d.matieres):
                continue
            key = (classe.id, prog.matiere_id)
            if key not in affectation:
                continue
            charge[affectation[key]] += nb_creneaux(prog.h_semaine)

    profs = []
    nb_ecarts = 0
    for p_id in sorted(d.profs):
        p = d.profs[p_id]
        h = charge[p_id]
        if h == 0 and p.h_contrat == 0:
            continue
        delta = h - p.h_contrat
        marge = p.h_contrat - h
        if delta > 0:
            nb_ecarts += 1
        is_c = _est_combleur(p)
        profs.append({
            "id":            p_id,
            "nom":           p.nom_complet,
            "nom_affichage": LIBELLE_COMBLEUR if is_c else p.nom_complet,
            "combleur":      is_c,
            "matieres":      list(p.matieres),
            "h_assignees":   h,
            "h_contrat":     p.h_contrat,
            "delta_h":       delta,
            "marge_h":       marge if marge > 0 else 0,
        })

    profs.sort(
        key=lambda r: (
            0 if r["combleur"] else 1,
            -max(r["delta_h"], 0),
            -r["h_assignees"] if r["combleur"] else -r["marge_h"],
            r["nom_affichage"],
        ),
    )

    h_combleurs = sum(p["h_assignees"] for p in profs if p["combleur"])

    return {
        "profs":                  profs,
        "erreur":                 None,
        "total_assigne":          sum(charge.values()),
        "total_contrat":          total_contrat,
        "nb_ecarts":              nb_ecarts,
        "nb_combleurs":           sum(1 for p in profs if p["combleur"]),
        "h_combleurs":            h_combleurs,
        "pre_assign_sans_combleur": PRE_ASSIGN_META.get("sans_combleur_ok", False),
    }


def heures_par_prof(solution: dict, d: DonneesCollege) -> dict[str, int]:
    """Créneaux enseignés par prof dans la solution (1 créneau = 1 h contrat)."""
    charge: dict[str, int] = defaultdict(int)
    for classe_cours in solution.values():
        for entry in classe_cours.values():
            p_id = entry[1]
            if p_id:
                charge[p_id] += 1
    return dict(charge)
