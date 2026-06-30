# File: probe_edt.py - Sondage CP-SAT minimal et messages d'infaisabilité EDT
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

from solver.constants import NUM_SEARCH_WORKERS
from solver.donnees import DonneesCollege, Preference
from solver.prefs_cp import _contrainte_max_heures_consec
from solver.salles import (
    _matiere_salle_type,
    capacite_par_etab_type,
    diagnostiquer_salles,
)

def verifier_creneaux_profs_disponibles(
    d: DonneesCollege,
    affectation: dict[tuple[str, str], str],
    besoins: dict[tuple[str, str], int],
) -> list[str]:
    """Blocage dur : plus de créneaux à placer que de cases horaires pour un prof."""
    creneaux_dispo: dict[str, int] = {
        p_id: sum(1 for t in d.creneaux if t.jour in p.jours_dispo)
        for p_id, p in d.profs.items()
    }
    charge: dict[str, int] = defaultdict(int)
    for (c_id, m_id), nb in besoins.items():
        charge[affectation[(c_id, m_id)]] += nb
    lignes: list[str] = []
    for p_id, nb in sorted(charge.items(), key=lambda x: -x[1]):
        disp = creneaux_dispo[p_id]
        if nb > disp:
            p = d.profs[p_id]
            lignes.append(
                f"{p.nom_complet} ({p_id}) : {nb} créneaux à placer "
                f"vs {disp} créneaux disponibles/semaine"
            )
    return lignes


def diagnostiquer_creneaux_profs(
    d: DonneesCollege,
    affectation: dict[tuple[str, str], str],
    besoins: dict[tuple[str, str], int],
) -> list[str]:
    """Surcharge créneaux / profs multi-classes — causes dures probables."""
    hard = verifier_creneaux_profs_disponibles(d, affectation, besoins)
    if hard:
        return hard

    classes_by_id = {c.id: c for c in d.classes}
    creneaux_dispo: dict[str, int] = {
        p_id: sum(1 for t in d.creneaux if t.jour in p.jours_dispo)
        for p_id, p in d.profs.items()
    }
    charge: dict[str, int] = defaultdict(int)
    cours_par_prof: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for (c_id, m_id), nb in besoins.items():
        p_id = affectation[(c_id, m_id)]
        charge[p_id] += nb
        cours_par_prof[p_id].append((c_id, m_id, nb))

    suspects: list[tuple[float, str, int, int, int]] = []
    for p_id, nb in charge.items():
        disp = creneaux_dispo[p_id]
        n_classes = len({c for c, m, h in cours_par_prof[p_id]})
        if n_classes >= 2:
            suspects.append((nb / max(disp, 1), p_id, nb, disp, n_classes))
    suspects.sort(reverse=True)

    lignes: list[str] = [
        "Conflit simultanéité : un prof ne peut pas enseigner deux classes "
        "au même créneau horaire."
    ]
    for _, p_id, nb, disp, n_classes in suspects[:8]:
        p = d.profs[p_id]
        detail = f"{nb} créneaux, {n_classes} classes, {disp} cases/sem."
        exemples = []
        for c_id, m_id, h in cours_par_prof[p_id][:3]:
            c = classes_by_id[c_id]
            abrev = d.matieres[m_id].abrev if m_id in d.matieres else m_id
            exemples.append(f"{c.nom}/{abrev} ({h}h)")
        if exemples:
            detail += " — ex. " + ", ".join(exemples)
        lignes.append(f"  └ {p.nom_complet} ({p_id}) : {detail}")

    return lignes


def _probe_edt_faisable(
    d: DonneesCollege,
    affectation: dict[tuple[str, str], str],
    besoins: dict[tuple[str, str], int],
    *,
    prefs_dures: bool,
    salles: bool,
    salle_assign: dict[tuple[str, str], str | None] | None = None,
    cap_etab: dict[tuple[str, str], int] | None = None,
) -> bool:
    """CP-SAT minimal : quotas + classes + profs (+ prefs dures, + salles)."""
    from ortools.sat.python import cp_model

    classes_by_id = {c.id: c for c in d.classes}
    model = cp_model.CpModel()
    x: dict[tuple[str, str, str], cp_model.IntVar] = {}
    y_spec: dict[tuple[str, str, str], cp_model.IntVar] = {}
    special_pairs: list[tuple[str, str]] = []

    for (c_id, m_id), nb in besoins.items():
        for t in d.creneaux:
            x[(c_id, m_id, t.id)] = model.new_bool_var(f"px_{c_id}_{m_id}_{t.id}")
        model.add(sum(x[(c_id, m_id, t.id)] for t in d.creneaux) == nb)
        if _matiere_salle_type(d, m_id):
            special_pairs.append((c_id, m_id))
            for t in d.creneaux:
                y_spec[(c_id, m_id, t.id)] = model.new_bool_var(f"pys_{c_id}_{m_id}_{t.id}")
                model.add(y_spec[(c_id, m_id, t.id)] <= x[(c_id, m_id, t.id)])

    for classe in d.classes:
        m_ids = [m_id for (c_id, m_id) in besoins if c_id == classe.id]
        for t in d.creneaux:
            at_slot = [x[(classe.id, m_id, t.id)] for m_id in m_ids]
            if len(at_slot) > 1:
                model.add_at_most_one(at_slot)

    prof_slots: dict[tuple[str, str], list] = defaultdict(list)
    for (c_id, m_id), p_id in affectation.items():
        if (c_id, m_id) not in besoins:
            continue
        for t in d.creneaux:
            prof_slots[(p_id, t.id)].append(x[(c_id, m_id, t.id)])
    for vars_list in prof_slots.values():
        if len(vars_list) > 1:
            model.add_at_most_one(vars_list)

    for (c_id, m_id), p_id in affectation.items():
        if (c_id, m_id) not in besoins:
            continue
        prof = d.profs[p_id]
        for t in d.creneaux:
            if t.jour not in prof.jours_dispo:
                model.add(x[(c_id, m_id, t.id)] == 0)

    if prefs_dures:
        prefs_by_prof: dict[str, list[Preference]] = defaultdict(list)
        for pref in d.preferences:
            prefs_by_prof[pref.prof_id].append(pref)
        for (c_id, m_id), p_id in affectation.items():
            if (c_id, m_id) not in besoins:
                continue
            classe = classes_by_id[c_id]
            for pref in prefs_by_prof.get(p_id, []):
                if pref.type == "creneau_bloque" and pref.priorite == "contrainte":
                    parts = pref.valeur.split(":")
                    if len(parts) < 3:
                        continue
                    jour_bloque = parts[0]
                    debut_bloque = f"{parts[1]}:{parts[2]}"
                    fin_bloque = f"{parts[3]}:{parts[4]}" if len(parts) >= 5 else "23:59"
                    for t in d.creneaux:
                        if t.jour == jour_bloque and debut_bloque <= t.debut < fin_bloque:
                            model.add(x[(c_id, m_id, t.id)] == 0)
                if pref.type == "niveau_refuse" and pref.priorite == "contrainte":
                    if classe.niveau == pref.valeur:
                        for t in d.creneaux:
                            model.add(x[(c_id, m_id, t.id)] == 0)
                if pref.type == "max_heures_consec" and pref.priorite == "contrainte":
                    _contrainte_max_heures_consec(
                        model, x, d, [(c_id, m_id)], int(pref.valeur)
                    )

    if salles and salle_assign is not None and cap_etab is not None:
        by_salle_fixe: dict[str, list[tuple[str, str]]] = defaultdict(list)
        pool_special: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        for (c_id, m_id) in special_pairs:
            sid = salle_assign.get((c_id, m_id))
            if sid:
                by_salle_fixe[sid].append((c_id, m_id))
            else:
                etab = classes_by_id[c_id].etab_id
                st = _matiere_salle_type(d, m_id)
                pool_special[(etab, st)].append((c_id, m_id))
        for s_id, pairs in by_salle_fixe.items():
            for t in d.creneaux:
                vars_s = [
                    y_spec[(c_id, m_id, t.id)]
                    for c_id, m_id in pairs
                    if (c_id, m_id, t.id) in y_spec
                ]
                if vars_s:
                    model.add_at_most_one(vars_s)
        for (etab_id, salle_type), pairs in pool_special.items():
            cap = cap_etab.get((etab_id, salle_type), 0)
            if cap == 0:
                continue
            for t in d.creneaux:
                vars_p = [
                    y_spec[(c_id, m_id, t.id)]
                    for c_id, m_id in pairs
                    if (c_id, m_id, t.id) in y_spec
                ]
                if vars_p:
                    model.add(sum(vars_p) <= cap)
        std_by_etab: dict[str, int] = defaultdict(int)
        for s in d.salles:
            if s.type == "standard":
                std_by_etab[s.etab_id] += 1
        etabs_besoin = {classes_by_id[c_id].etab_id for c_id, _ in besoins}
        for etab_id in etabs_besoin:
            cap_std = std_by_etab.get(etab_id, 0)
            if cap_std == 0:
                continue
            for t in d.creneaux:
                all_x = [x[(c_id, m_id, t.id)] for (c_id, m_id) in besoins
                         if classes_by_id[c_id].etab_id == etab_id]
                all_yspec = [y_spec[(c_id, m_id, t.id)] for (c_id, m_id) in special_pairs
                             if classes_by_id[c_id].etab_id == etab_id
                             and (c_id, m_id, t.id) in y_spec]
                if all_x:
                    model.add(sum(all_x) - sum(all_yspec) <= cap_std)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15.0
    if NUM_SEARCH_WORKERS:
        solver.parameters.num_search_workers = NUM_SEARCH_WORKERS
    st = solver.solve(model)
    return st in (cp_model.OPTIMAL, cp_model.FEASIBLE)


def message_infaisable_resoudre(
    d: DonneesCollege,
    affectation: dict[tuple[str, str], str],
    besoins: dict[tuple[str, str], int],
    salle_assign: dict[tuple[str, str], str | None],
    cap_etab: dict[tuple[str, str], int],
) -> str:
    """Message d'erreur CP-SAT : cause principale d'abord, salles en annexe."""
    sections: list[str] = []

    ok_profs = _probe_edt_faisable(
        d, affectation, besoins, prefs_dures=False, salles=False
    )
    ok_prefs = ok_profs and _probe_edt_faisable(
        d, affectation, besoins, prefs_dures=True, salles=False
    )
    ok_salles = ok_prefs and _probe_edt_faisable(
        d, affectation, besoins,
        prefs_dures=True, salles=True,
        salle_assign=salle_assign, cap_etab=cap_etab,
    )

    if not ok_profs:
        sections.append(
            "Emploi du temps infaisable (profs / créneaux) :\n  \u2022 "
            + "\n  \u2022 ".join(
                diagnostiquer_creneaux_profs(d, affectation, besoins)
            )
        )
    elif not ok_prefs:
        sections.append(
            "Emploi du temps infaisable — préférences en contrainte dure "
            "(creneau_bloque, niveau_refuse, max_heures_consec, regroupement…) "
            "ou regroupement inter-établissements."
        )
    elif not ok_salles:
        bloquant, _info = diagnostiquer_salles(d, besoins)
        sections.append(
            "Emploi du temps infaisable — salles saturées par créneau :\n  \u2022 "
            + "\n  \u2022 ".join(bloquant or ["capacité salle insuffisante à l'horaire"])
        )
    else:
        sections.append(
            "Emploi du temps infaisable — contraintes souples ou objectif "
            "(essayez d'assouplir les préférences)."
        )

    _bloquant, info_salles = diagnostiquer_salles(d, besoins)
    if info_salles:
        sections.append(
            "Salles (repli automatique, non bloquant) :\n  \u2139 "
            + "\n  \u2139 ".join(info_salles)
        )

    return "\n\n".join(sections)
