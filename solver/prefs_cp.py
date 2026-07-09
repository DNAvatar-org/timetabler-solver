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

def _contrainte_debut_pas_avant(
    model,
    x: dict,
    d: DonneesCollege,
    assignments: list[tuple[str, str]],
    heure_min: str,
) -> None:
    """Contrainte dure : aucun cours ne commence avant heure_min (HH:MM)."""
    for c_id, m_id in assignments:
        for t in d.creneaux:
            if t.debut >= heure_min:
                continue
            key = (c_id, m_id, t.id)
            if key in x:
                model.add(x[key] == 0)


def _creneaux_prof_solution(
    solution: dict,
    prof_id: str,
    t_map: dict[str, Creneau],
) -> list[tuple[str, str, Creneau]]:
    out: list[tuple[str, str, Creneau]] = []
    for c_id, cours in solution.items():
        for t_id, entry in cours.items():
            if len(entry) >= 3:
                m_id, p_id = entry[0], entry[1]
            else:
                m_id, p_id = entry[0], entry[1]
            if p_id != prof_id:
                continue
            t = t_map.get(t_id)
            if t:
                out.append((c_id, m_id, t))
    return out


def _viole_max_heures_consec(
    slots: list[tuple[str, str, Creneau]],
    d: DonneesCollege,
    max_h: int,
) -> bool:
    idx = {cr.id: i for i, cr in enumerate(d.creneaux)}
    by_jour: dict[str, list[int]] = defaultdict(list)
    for _, _, t in slots:
        if t.id in idx:
            by_jour[t.jour].append(idx[t.id])
    for indices in by_jour.values():
        indices.sort()
        streak = 1
        for i in range(1, len(indices)):
            if indices[i] == indices[i - 1] + 1:
                streak += 1
                if streak > max_h:
                    return True
            else:
                streak = 1
    return False


def contraintes_dures_violees(
    solution: dict,
    d: DonneesCollege,
) -> list[dict]:
    """Préférences priorité=contrainte non respectées dans une solution trouvée."""
    t_map = {cr.id: cr for cr in d.creneaux}
    classes_by_id = {c.id: c for c in d.classes}
    vios: list[dict] = []

    for pref in d.preferences:
        if pref.priorite != "contrainte":
            continue
        prof = d.profs.get(pref.prof_id)
        if not prof:
            continue
        slots = _creneaux_prof_solution(solution, pref.prof_id, t_map)
        violation = False
        note = ""

        if pref.type == "jour_libre":
            violation = any(t.jour == pref.valeur for _, _, t in slots)
        elif pref.type == "debut_pas_avant":
            if ":" not in pref.valeur:
                violation = bool(slots)
                note = " (valeur invalide : attend une heure HH:MM, pas un jour)"
            else:
                violation = any(t.debut < pref.valeur for _, _, t in slots)
        elif pref.type == "creneau_bloque":
            parts = pref.valeur.split(":")
            if len(parts) >= 3:
                jour_b = parts[0]
                deb_b = f"{parts[1]}:{parts[2]}"
                fin_b = f"{parts[3]}:{parts[4]}" if len(parts) >= 5 else "23:59"
                violation = any(
                    t.jour == jour_b and deb_b <= t.debut < fin_b
                    for _, _, t in slots
                )
        elif pref.type == "niveau_refuse":
            violation = any(
                classes_by_id.get(c_id) and classes_by_id[c_id].niveau == pref.valeur
                for c_id, _, _ in slots
            )
        elif pref.type == "max_heures_consec":
            try:
                max_h = int(pref.valeur)
                violation = _viole_max_heures_consec(slots, d, max_h)
            except ValueError:
                violation = True
                note = " (valeur invalide : entier attendu)"

        if violation:
            vios.append({
                "prof": prof.nom_complet,
                "prof_id": pref.prof_id,
                "type": pref.type,
                "valeur": pref.valeur,
                "note": note,
            })
    return vios


def message_contraintes_violees_apres_solve(
    solution: dict,
    d: DonneesCollege,
) -> str | None:
    """Message d'échec si une contrainte dure est KO dans le planning retourné."""
    vios = contraintes_dures_violees(solution, d)
    if not vios:
        return None
    from solver.probe_edt import message_infaisabilite_structure

    lignes_effacables = [
        "Contraintes à assouplir ou effacer pour débloquer le planning "
        "(passer en haute/moyenne/basse, corriger la valeur, ou supprimer la ligne) :",
    ]
    for v in vios:
        ligne = f"  • {v['prof']} ({v['prof_id']}) — {v['type']} = {v['valeur']}"
        if v.get("note"):
            ligne += v["note"]
        lignes_effacables.append(ligne)

    cause = (
        "Cause immédiate : le solveur a trouvé un emploi du temps mais une ou plusieurs "
        "préférences « contrainte » restent violées — résultat refusé.\n\n"
        + "\n".join(lignes_effacables)
    )
    return message_infaisabilite_structure(d, affectation=None, besoins=None, cause=cause)


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
