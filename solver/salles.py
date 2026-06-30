# File: salles.py - Capacités salles, affectation nominative, diagnostic
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
from collections import defaultdict

from solver.donnees import DonneesCollege, Salle
from solver.programme import _ATELIER_FILIERE

def capacite_par_type(salles: list[Salle]) -> dict[str, int]:
    """Nombre de salles disponibles par type (tous établissements — legacy)."""
    cap: dict[str, int] = defaultdict(int)
    for s in salles:
        cap[s.type] += 1
    return dict(cap)


def capacite_par_etab_type(d: DonneesCollege) -> dict[tuple[str, str], int]:
    """Nombre de salles par (etab_id, type)."""
    cap: dict[tuple[str, str], int] = defaultdict(int)
    for s in d.salles:
        cap[(s.etab_id, s.type)] += 1
    return dict(cap)


def _salle_fixe_id(
    d: DonneesCollege,
    etab_id: str,
    salle_type: str,
    specificite: str | None,
) -> str | None:
    """Salle nominative fixe (salles.stabilite=fixe) pour ce type / filière."""
    spec = (specificite or "").lower()
    for s in d.salles:
        if s.etab_id != etab_id or s.type != salle_type or s.stabilite != "fixe":
            continue
        if salle_type in _ATELIER_FILIERE:
            mot = _ATELIER_FILIERE[salle_type]
            if mot not in spec:
                continue
        return s.id
    return None


def construire_affectation_salles(
    d: DonneesCollege,
    besoins: dict[tuple[str, str], int],
) -> dict[tuple[str, str], str | None]:
    """
    Salle nominative pour affichage si salles.stabilite=fixe.
    None = pool (spécial ou standard selon placement CP-SAT).
    """
    classes_by_id = {c.id: c for c in d.classes}
    assign: dict[tuple[str, str], str | None] = {}
    for (c_id, m_id) in besoins:
        classe = classes_by_id[c_id]
        mat = d.matieres[m_id]
        if mat.salle_type:
            assign[(c_id, m_id)] = _salle_fixe_id(
                d, classe.etab_id, mat.salle_type, classe.specificite
            )
        else:
            assign[(c_id, m_id)] = None
    return assign


def _matiere_salle_type(d: DonneesCollege, m_id: str) -> str | None:
    st = d.matieres[m_id].salle_type
    if st and st != "standard":
        return st
    return None


def _premiere_salle_id(d: DonneesCollege, etab_id: str, salle_type: str) -> str:
    for s in d.salles:
        if s.etab_id == etab_id and s.type == salle_type:
            return s.id
    return ""


def diagnostiquer_salles(
    d: DonneesCollege,
    besoins: dict[tuple[str, str], int],
) -> tuple[list[str], list[str]]:
    """
    Retourne (bloquants, infos).
    Débordement atelier/labo → standard = info ; saturation standard ou totale = bloquant.
    """
    if not besoins:
        return [], []
    nb_cren = len(d.creneaux)
    if nb_cren == 0:
        return ["Aucun créneau cours défini."], []

    classes_by_id = {c.id: c for c in d.classes}
    cap_etab = capacite_par_etab_type(d)
    cap_total: dict[str, int] = defaultdict(int)
    for s in d.salles:
        cap_total[s.etab_id] += 1
    blocking: list[str] = []
    info: list[str] = []

    by_etab: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for (c_id, m_id), h in besoins.items():
        etab = classes_by_id[c_id].etab_id
        st = d.matieres[m_id].salle_type or "standard"
        by_etab[etab][st] += h

    for etab_id in sorted(by_etab):
        pools = by_etab[etab_id]
        total_h = sum(pools.values())
        max_total = cap_total[etab_id] * nb_cren
        if total_h > max_total:
            blocking.append(
                f"Étab {etab_id} : {total_h}h programme vs {max_total}h max "
                f"({cap_total[etab_id]} salle(s) × {nb_cren} créneaux)"
            )
        overflow_std = 0
        for st, h in sorted(pools.items()):
            if st == "standard":
                continue
            cap = cap_etab.get((etab_id, st), 0)
            max_h = cap * nb_cren
            if cap == 0 and h > 0:
                overflow_std += h
                info.append(
                    f"Étab {etab_id} : {h}h « {st} » sans salle dédiée → repli standard"
                )
            elif h > max_h:
                exc = h - max_h
                overflow_std += exc
                info.append(
                    f"Étab {etab_id}, {st} : {h}h dont {exc}h en salle standard "
                    f"({cap}×{nb_cren} créneaux atelier saturés)"
                )
        std_h = pools.get("standard", 0) + overflow_std
        cap_std = cap_etab.get((etab_id, "standard"), 0)
        max_std = cap_std * nb_cren
        if std_h > max_std:
            deficit = std_h - max_std
            blocking.append(
                f"Étab {etab_id}, standard : {std_h}h à placer "
                f"vs {max_std}h max ({cap_std} salle(s) × {nb_cren} créneaux)"
                f" — ajouter ~{math.ceil(deficit / nb_cren)} salle(s) standard "
                f"ou réduire le programme"
            )
    return blocking, info


def _nom_salle_cours(
    d: DonneesCollege,
    s_id: str | None,
    c_id: str,
    m_id: str,
    en_special: bool,
) -> str:
    salles_by_id = {s.id: s for s in d.salles}
    if en_special and s_id and s_id in salles_by_id:
        return salles_by_id[s_id].nom
    classe = next(c for c in d.classes if c.id == c_id)
    if en_special:
        mat = d.matieres[m_id]
        if mat.salle_type:
            sid = _salle_fixe_id(d, classe.etab_id, mat.salle_type, classe.specificite)
            if sid and sid in salles_by_id:
                return salles_by_id[sid].nom
    for s in d.salles:
        if s.etab_id == classe.etab_id and s.type == "standard":
            return s.nom
    return ""
