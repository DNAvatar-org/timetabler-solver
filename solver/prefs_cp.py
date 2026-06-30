# File: prefs_cp.py - Préférences professeurs — contraintes et pénalités CP-SAT
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

from solver.constants import JOURS
from solver.donnees import Creneau, DonneesCollege, Preference

def _penalite_et(
    x: dict,
    d: DonneesCollege,
    c_id: str,
    m_id: str,
    pref: Preference,
    poids: int,
    penalites: list[tuple[int, object]],
) -> None:
    """Pénalise chaque créneau qui viole une préf souple (jour_libre, debut_pas_avant, creneau_bloque)."""
    if pref.type == "jour_libre":
        for t in d.creneaux:
            if t.jour != pref.valeur:
                continue
            key = (c_id, m_id, t.id)
            if key in x:
                penalites.append((poids, x[key]))
    elif pref.type == "debut_pas_avant":
        for t in d.creneaux:
            if t.debut >= pref.valeur:
                continue
            key = (c_id, m_id, t.id)
            if key in x:
                penalites.append((poids, x[key]))
    elif pref.type == "creneau_bloque":
        parts = pref.valeur.split(":")
        if len(parts) < 3:
            return
        jour_bloque  = parts[0]
        debut_bloque = f"{parts[1]}:{parts[2]}"
        fin_bloque   = f"{parts[3]}:{parts[4]}" if len(parts) >= 5 else "23:59"
        for t in d.creneaux:
            if t.jour == jour_bloque and debut_bloque <= t.debut < fin_bloque:
                key = (c_id, m_id, t.id)
                if key in x:
                    penalites.append((poids, x[key]))


def _pref_violee_prof(
    model,
    x: dict,
    d: DonneesCollege,
    assignments: list[tuple[str, str]],
    pref: Preference,
    tag: str,
):
    """Variable booléenne = 1 si la préférence est violée (niveau prof)."""
    vars_cibles: list = []
    if pref.type == "jour_libre":
        for t in d.creneaux:
            if t.jour != pref.valeur:
                continue
            for c_id, m_id in assignments:
                key = (c_id, m_id, t.id)
                if key in x:
                    vars_cibles.append(x[key])
    elif pref.type == "debut_pas_avant":
        for t in d.creneaux:
            if t.debut >= pref.valeur:
                continue
            for c_id, m_id in assignments:
                key = (c_id, m_id, t.id)
                if key in x:
                    vars_cibles.append(x[key])
    else:
        return None

    if not vars_cibles:
        return None

    viole = model.new_bool_var(tag)
    model.add(sum(vars_cibles) >= 1).only_enforce_if(viole)
    model.add(sum(vars_cibles) == 0).only_enforce_if(viole.Not())
    return viole


def _penalite_ou(
    model,
    x: dict,
    d: DonneesCollege,
    p_id: str,
    assignments: list[tuple[str, str]],
    pref_a: Preference,
    pref_b: Preference,
    poids: int,
    penalites: list[tuple[int, object]],
) -> None:
    """Pénalise si les deux préférences d'un OU sont violées simultanément."""
    va = _pref_violee_prof(model, x, d, assignments, pref_a, f"ou_a_{p_id}_{len(penalites)}")
    vb = _pref_violee_prof(model, x, d, assignments, pref_b, f"ou_b_{p_id}_{len(penalites)}")
    if va is None and vb is None:
        return
    if va is None:
        penalites.append((poids, vb))
        return
    if vb is None:
        penalites.append((poids, va))
        return

    both = model.new_bool_var(f"ou_viol_{p_id}_{len(penalites)}")
    model.add_bool_and([va, vb]).only_enforce_if(both)
    model.add_bool_or([va.Not(), vb.Not()]).only_enforce_if(both.Not())
    penalites.append((poids, both))


def _contrainte_max_heures_consec(
    model,
    x: dict,
    d: DonneesCollege,
    assignments: list[tuple[str, str]],
    max_h: int,
) -> None:
    """Contrainte dure : pas plus de max_h créneaux consécutifs par jour."""
    by_jour: dict[str, list[Creneau]] = defaultdict(list)
    for t in d.creneaux:
        by_jour[t.jour].append(t)

    for jour in JOURS:
        slots = sorted(by_jour.get(jour, []), key=lambda t: t.debut)
        if len(slots) <= max_h:
            continue
        for start in range(len(slots) - max_h):
            window = slots[start:start + max_h + 1]
            busy = []
            for t in window:
                at_t = [
                    x[(c_id, m_id, t.id)]
                    for c_id, m_id in assignments
                    if (c_id, m_id, t.id) in x
                ]
                if at_t:
                    busy.append(sum(at_t))
            if busy:
                model.add(sum(busy) <= max_h)
