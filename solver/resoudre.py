# File: resoudre.py - Modèle CP-SAT emploi du temps (créneaux, salles, prefs)
# Desc: En français, dans l'architecture emploi du temps, je suis…
# Version 1.0.2
# Copyright 2025 DNAvatar.org - Arnaud Maignan
# Licensed under Apache License 2.0 with Commons Clause.
# See LICENSE_HEADER.txt for full terms.
# Date: June 27, 2025
# Logs:
# - Split from solve_ecoles.py monolith
# - Error messages: contrat horaire (not h_contrat)
# - Fix missing imports after split (combinations, JOURS, DistanceEtab)

from __future__ import annotations
from collections import defaultdict
from itertools import combinations

from solver.constants import (
    JOURS,
    NUM_SEARCH_WORKERS,
    POIDS,
    POIDS_CONTRAT_SLACK,
    _push_progress,
    _SolveProgressCallback,
)
from solver.donnees import DonneesCollege, DistanceEtab, Preference
from solver.prefs_cp import (
    _contrainte_debut_pas_avant,
    _contrainte_max_heures_consec,
    _penalite_et,
    _penalite_ou,
    message_contraintes_violees_apres_solve,
)
from solver.pre_assign import pre_assigner_profs
from solver.probe_edt import message_infaisable_resoudre, verifier_creneaux_profs_disponibles
from solver.programme import _niveau_match, matiere_applicable, nb_creneaux
from solver.salles import (
    _matiere_salle_type,
    _premiere_salle_id,
    capacite_par_etab_type,
    construire_affectation_salles,
    diagnostiquer_salles,
)
from solver.staffing_bilan import _message_ecarts_contrats

# Réexport mutable pour stop_active_solver
import solver.constants as _solver_constants

def resoudre(d: DonneesCollege) -> tuple[dict, float, float, int, str] | None:
    from ortools.sat.python import cp_model

    _push_progress({"phase": "preassign", "msg": "Affectation des profs…"})
    affectation = pre_assigner_profs(d)
    print("  [1/4] Pré-affectation profs — OK")
    _push_progress({"phase": "preassign", "ok": True})

    charge_greedy: dict[str, int] = defaultdict(int)
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
            charge_greedy[affectation[key]] += nb_creneaux(prog.h_semaine)

    # Surcharge uniquement (assigné > contrat) = bloquant
    for p_id, h in charge_greedy.items():
        if h > d.profs[p_id].h_contrat:
            raise ValueError(
                f"Surcharge prof : {d.profs[p_id].nom_complet} "
                f"{h}h assignées / {d.profs[p_id].h_contrat}h contrat horaire"
            )

    cap_etab = capacite_par_etab_type(d)

    model = cp_model.CpModel()

    # ---- Variables -------------------------------------------------------
    # x[(c_id, m_id, t_id)] = 1 si la classe c a matière m au créneau t
    x: dict[tuple[str, str, str], cp_model.IntVar] = {}
    besoins: dict[tuple[str, str], int] = {}

    for classe in d.classes:
        for prog in d.programme:
            if not _niveau_match(prog, classe):
                continue
            m_id = prog.matiere_id
            if (classe.id, m_id) not in affectation:
                continue
            nb = nb_creneaux(prog.h_semaine)
            besoins[(classe.id, m_id)] = nb
            for t in d.creneaux:
                x[(classe.id, m_id, t.id)] = model.new_bool_var(
                    f"x_{classe.id}_{m_id}_{t.id}"
                )

    print(f"  Variables     : {len(x):,}")
    print(f"  Paires (c,m)  : {len(besoins)}")

    salle_assign = construire_affectation_salles(d, besoins)
    diag_bloquant, diag_info = diagnostiquer_salles(d, besoins)
    if diag_bloquant:
        extra = ""
        if diag_info:
            extra = "\n  ℹ " + "\n  ℹ ".join(diag_info)
        raise ValueError(
            "Salles insuffisantes pour le programme :\n  • "
            + "\n  • ".join(diag_bloquant)
            + extra
        )
    for line in diag_info:
        print(f"  ℹ Salles : {line}")

    surcharge_cren = verifier_creneaux_profs_disponibles(d, affectation, besoins)
    if surcharge_cren:
        extra = ""
        if diag_info:
            extra = (
                "\n\nSalles (repli automatique, non bloquant) :\n  ℹ "
                + "\n  ℹ ".join(diag_info)
            )
        raise ValueError(
            "Emploi du temps impossible (grille horaire insuffisante) :\n  • "
            + "\n  • ".join(surcharge_cren)
            + extra
        )
    print("  [2/4] Vérifications salles + créneaux — OK")

    classes_by_id = {c.id: c for c in d.classes}

    # ---- Contraintes dures -----------------------------------------------

    # 1. Quota : chaque (classe, matière) a exactement nb créneaux par semaine
    for (c_id, m_id), nb in besoins.items():
        model.add(sum(x[(c_id, m_id, t.id)] for t in d.creneaux) == nb)

    # 2. Une classe a au plus un cours par créneau
    for classe in d.classes:
        m_ids = [m_id for (c_id, m_id) in besoins if c_id == classe.id]
        for t in d.creneaux:
            at_slot = [x[(classe.id, m_id, t.id)] for m_id in m_ids]
            if len(at_slot) > 1:
                model.add_at_most_one(at_slot)

    # 3. Un prof ne peut pas enseigner à deux classes au même créneau
    prof_slots: dict[tuple[str, str], list] = defaultdict(list)
    for (c_id, m_id) in affectation:
        p_id = affectation[(c_id, m_id)]
        for t in d.creneaux:
            prof_slots[(p_id, t.id)].append(x[(c_id, m_id, t.id)])

    for (p_id, t_id), vars_list in prof_slots.items():
        if len(vars_list) > 1:
            model.add_at_most_one(vars_list)

    # 4. Prof indisponible → aucun cours ce jour-là
    for (c_id, m_id), p_id in affectation.items():
        prof = d.profs[p_id]
        for t in d.creneaux:
            if t.jour not in prof.jours_dispo:
                model.add(x[(c_id, m_id, t.id)] == 0)

    # 4b. Créneaux bloqués explicitement (creneau_bloque) → contrainte dure
    prefs_by_prof_all: dict[str, list[Preference]] = defaultdict(list)
    for pref in d.preferences:
        prefs_by_prof_all[pref.prof_id].append(pref)

    for (c_id, m_id), p_id in affectation.items():
        for pref in prefs_by_prof_all.get(p_id, []):
            if pref.type != "creneau_bloque" or pref.priorite != "contrainte":
                continue
            parts = pref.valeur.split(":")
            if len(parts) < 3:
                continue
            jour_bloque = parts[0]
            debut_bloque = f"{parts[1]}:{parts[2]}"
            fin_bloque = f"{parts[3]}:{parts[4]}" if len(parts) >= 5 else "23:59"
            for t in d.creneaux:
                if t.jour == jour_bloque and debut_bloque <= t.debut < fin_bloque:
                    model.add(x[(c_id, m_id, t.id)] == 0)

    # 4c. Niveaux refusés → contrainte dure : x=0 pour les classes de ce niveau
    for (c_id, m_id), p_id in affectation.items():
        classe = classes_by_id.get(c_id)
        if classe is None:
            continue
        for pref in prefs_by_prof_all.get(p_id, []):
            if pref.type != "niveau_refuse" or pref.priorite != "contrainte":
                continue
            if classe.niveau == pref.valeur:
                for t in d.creneaux:
                    if (c_id, m_id, t.id) in x:
                        model.add(x[(c_id, m_id, t.id)] == 0)

    # 4d. Début pas avant → contrainte dure (aucun cours avant l'heure indiquée)
    for (c_id, m_id), p_id in affectation.items():
        if (c_id, m_id) not in besoins:
            continue
        assignments_one = [(c_id, m_id)]
        for pref in prefs_by_prof_all.get(p_id, []):
            if pref.type != "debut_pas_avant" or pref.priorite != "contrainte":
                continue
            _contrainte_debut_pas_avant(model, x, d, assignments_one, pref.valeur)

    # 5–6. Salles : spécial (fixe ou pool) ou repli standard par créneau
    y_spec: dict[tuple[str, str, str], cp_model.IntVar] = {}
    special_pairs: list[tuple[str, str]] = []

    for (c_id, m_id) in besoins:
        if not _matiere_salle_type(d, m_id):
            continue
        special_pairs.append((c_id, m_id))
        for t in d.creneaux:
            y_spec[(c_id, m_id, t.id)] = model.new_bool_var(
                f"ys_{c_id}_{m_id}_{t.id}"
            )
            model.add(y_spec[(c_id, m_id, t.id)] <= x[(c_id, m_id, t.id)])

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
                     if classes_by_id[c_id].etab_id == etab_id
                     and (c_id, m_id, t.id) in x]
            all_yspec = [y_spec[(c_id, m_id, t.id)] for (c_id, m_id) in special_pairs
                         if classes_by_id[c_id].etab_id == etab_id
                         and (c_id, m_id, t.id) in y_spec]
            if all_x:
                model.add(sum(all_x) - sum(all_yspec) <= cap_std)

    # 7. Contrat : au plus h_contrat créneaux ; marge minimisée dans l'objectif
    prof_all_vars: dict[str, list] = defaultdict(list)
    for (c_id, m_id), p_id in affectation.items():
        for t in d.creneaux:
            if (c_id, m_id, t.id) in x:
                prof_all_vars[p_id].append(x[(c_id, m_id, t.id)])

    slacks_contrat: list = []
    for p_id, vars_list in prof_all_vars.items():
        h = d.profs[p_id].h_contrat
        assigned = sum(vars_list)
        model.add(assigned <= h)
        slack = model.new_int_var(0, h, f"slack_contrat_{p_id}")
        model.add(assigned + slack == h)
        slacks_contrat.append(slack)

    # ---- Contraintes souples (pénalités dans l'objectif) -----------------
    prefs_by_prof: dict[str, list[Preference]] = defaultdict(list)
    for pref in d.preferences:
        prefs_by_prof[pref.prof_id].append(pref)

    # Assignments par prof (pour les pénalités OU qui opèrent sur le prof entier)
    prof_assignments: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (c_id, m_id), p_id in affectation.items():
        prof_assignments[p_id].append((c_id, m_id))

    penalites: list[tuple[int, object]] = []

    _PREFS_DURES = {"max_heures_consec", "niveau_refuse", "etab_requis"}
    for p_id, prefs_p in prefs_by_prof.items():
        assignments = prof_assignments[p_id]
        for pref in prefs_p:
            if pref.type == "max_heures_consec" and pref.priorite == "contrainte":
                _contrainte_max_heures_consec(
                    model, x, d, assignments, int(pref.valeur)
                )

    for p_id, prefs_p in prefs_by_prof.items():
        assignments = prof_assignments[p_id]
        i = 0
        while i < len(prefs_p):
            pref_a = prefs_p[i]
            is_pref_dure = pref_a.priorite == "contrainte" and pref_a.type in (
                "jour_libre", "debut_pas_avant", "creneau_bloque",
            )
            if pref_a.type in _PREFS_DURES or pref_a.type == "grouper_cours" or is_pref_dure:
                i += 1
                continue
            poids  = POIDS.get(pref_a.priorite, 1)
            next_ou = i + 1 < len(prefs_p) and prefs_p[i + 1].operateur == 'OU'

            if next_ou:
                pref_b = prefs_p[i + 1]
                if pref_b.type not in _PREFS_DURES and pref_b.type != "grouper_cours":
                    _penalite_ou(model, x, d, p_id, assignments, pref_a, pref_b, poids, penalites)
                i += 2
            else:
                for c_id, m_id in assignments:
                    _penalite_et(x, d, c_id, m_id, pref_a, poids, penalites)
                i += 1

    # Regroupement inter-établissements (contraintes issues du tableau distances)
    if d.distances_etab:
        dist_by_pair: dict[frozenset, DistanceEtab] = {}
        for de in d.distances_etab:
            dist_by_pair[frozenset([de.etab_a, de.etab_b])] = de

        prof_etabs: dict[str, set[str]] = defaultdict(set)
        asgn_etab: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for (cid, mid), pid in affectation.items():
            cls = classes_by_id.get(cid)
            if cls and cls.etab_id:
                prof_etabs[pid].add(cls.etab_id)
                asgn_etab[pid][cls.etab_id].append((cid, mid))

        for pid, etabs in prof_etabs.items():
            if len(etabs) < 2:
                continue
            for ea, eb in combinations(sorted(etabs), 2):
                de = dist_by_pair.get(frozenset([ea, eb]))
                if not de:
                    continue
                aea = asgn_etab[pid].get(ea, [])
                aeb = asgn_etab[pid].get(eb, [])
                for jour in JOURS:
                    if de.regroupement_demi:
                        for hname, cutoff, use_lt in [("mat", "12:00", True), ("apm", "13:00", False)]:
                            if use_lt:
                                va = [x[(c, m, t.id)] for c, m in aea for t in d.creneaux
                                      if t.jour == jour and t.debut < cutoff and (c, m, t.id) in x]
                                vb = [x[(c, m, t.id)] for c, m in aeb for t in d.creneaux
                                      if t.jour == jour and t.debut < cutoff and (c, m, t.id) in x]
                            else:
                                va = [x[(c, m, t.id)] for c, m in aea for t in d.creneaux
                                      if t.jour == jour and t.debut >= cutoff and (c, m, t.id) in x]
                                vb = [x[(c, m, t.id)] for c, m in aeb for t in d.creneaux
                                      if t.jour == jour and t.debut >= cutoff and (c, m, t.id) in x]
                            if not va or not vb:
                                continue
                            ha = model.new_bool_var(f"hd_{pid}_{ea}_{jour}_{hname}")
                            model.add(sum(va) >= 1).only_enforce_if(ha)
                            model.add(sum(va) == 0).only_enforce_if(ha.Not())
                            hb = model.new_bool_var(f"hd_{pid}_{eb}_{jour}_{hname}")
                            model.add(sum(vb) >= 1).only_enforce_if(hb)
                            model.add(sum(vb) == 0).only_enforce_if(hb.Not())
                            sw = model.new_bool_var(f"sw_d_{pid}_{ea}_{eb}_{jour}_{hname}")
                            model.add_bool_and([ha, hb]).only_enforce_if(sw)
                            model.add_bool_or([ha.Not(), hb.Not()]).only_enforce_if(sw.Not())
                            prio = de.regroupement_demi
                            if prio == "contrainte":
                                model.add(sw == 0)
                            elif prio in POIDS:
                                penalites.append((POIDS[prio], sw))
                    if de.regroupement_jour:
                        vja = [x[(c, m, t.id)] for c, m in aea for t in d.creneaux
                               if t.jour == jour and (c, m, t.id) in x]
                        vjb = [x[(c, m, t.id)] for c, m in aeb for t in d.creneaux
                               if t.jour == jour and (c, m, t.id) in x]
                        if not vja or not vjb:
                            continue
                        hja = model.new_bool_var(f"hj_{pid}_{ea}_{jour}")
                        model.add(sum(vja) >= 1).only_enforce_if(hja)
                        model.add(sum(vja) == 0).only_enforce_if(hja.Not())
                        hjb = model.new_bool_var(f"hj_{pid}_{eb}_{jour}")
                        model.add(sum(vjb) >= 1).only_enforce_if(hjb)
                        model.add(sum(vjb) == 0).only_enforce_if(hjb.Not())
                        swj = model.new_bool_var(f"sw_j_{pid}_{ea}_{eb}_{jour}")
                        model.add_bool_and([hja, hjb]).only_enforce_if(swj)
                        model.add_bool_or([hja.Not(), hjb.Not()]).only_enforce_if(swj.Not())
                        prio = de.regroupement_jour
                        if prio == "contrainte":
                            model.add(swj == 0)
                        elif prio in POIDS:
                            penalites.append((POIDS[prio], swj))

    for slack in slacks_contrat:
        penalites.append((POIDS_CONTRAT_SLACK, slack))

    if penalites:
        model.minimize(sum(w * v for w, v in penalites))

    print(f"  [3/4] Modèle CP-SAT — {len(x):,} variables, résolution (60 s max)…")
    _push_progress({"phase": "build", "msg": f"{len(x):,} variables"})

    # ---- Résolution -------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60.0
    if NUM_SEARCH_WORKERS:
        solver.parameters.num_search_workers = NUM_SEARCH_WORKERS

    print("\n  Résolution CP-SAT en cours…")
    _push_progress({"phase": "solve", "msg": "Résolution en cours…"})
    status = cp_model.UNKNOWN
    _solver_constants._ACTIVE_SOLVER = solver           # exposé pour /api/stop
    try:
        status = solver.solve(model, _SolveProgressCallback())
    finally:
        _push_progress({"phase": "done", "ok": status in (cp_model.OPTIMAL, cp_model.FEASIBLE)})
        _solver_constants._ACTIVE_SOLVER = None
    status_name = solver.status_name(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"  ✗ Pas de solution ({status_name})")
        if status == cp_model.UNKNOWN:
            raise ValueError(
                "Résolution interrompue (STOP) — OR-Tools n'avait pas encore "
                "trouvé de solution faisable. Attendez la fin du calcul ou "
                "corrigez les contraintes avant d'arrêter."
            )
        raise ValueError(message_infaisable_resoudre(
            d, affectation, besoins, salle_assign, cap_etab,
        ))

    qualite = "optimale" if status == cp_model.OPTIMAL else "réalisable (partielle)"
    print(f"  ✓ Solution {qualite}")
    print(f"  Score pénalités : {int(solver.objective_value)}")
    print(f"  Temps résolution : {solver.wall_time:.2f}s")

    # Propagations réelles OR-Tools (binary + integer)
    _num_propagations = 0
    try:
        _resp = solver._CpSolver__solution_response  # type: ignore[attr-defined]
        _num_propagations = int(_resp.num_binary_propagations + _resp.num_integer_propagations)
    except Exception:
        _num_propagations = solver.NumBranches()
    print(f"  Propagations OR-Tools : {_num_propagations:,}")

    # ---- Extraction de la solution ----------------------------------------
    solution: dict[str, dict[str, tuple]] = defaultdict(dict)
    for (c_id, m_id, t_id), var in x.items():
        if not solver.value(var):
            continue
        p_id = affectation[(c_id, m_id)]
        st = _matiere_salle_type(d, m_id)
        if st:
            en_special = bool(solver.value(y_spec[(c_id, m_id, t_id)]))
            if en_special:
                sid = salle_assign.get((c_id, m_id)) or _premiere_salle_id(
                    d, classes_by_id[c_id].etab_id, st
                )
            else:
                sid = _premiere_salle_id(d, classes_by_id[c_id].etab_id, "standard")
        else:
            sid = _premiere_salle_id(d, classes_by_id[c_id].etab_id, "standard")
        solution[c_id][t_id] = (m_id, p_id, sid)

    msg_contraintes = message_contraintes_violees_apres_solve(dict(solution), d)
    if msg_contraintes:
        raise ValueError(msg_contraintes)

    return dict(solution), solver.objective_value, solver.wall_time, _num_propagations, status_name
