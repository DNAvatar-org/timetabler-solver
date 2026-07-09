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

from solver.pre_assign import (
    _hall_violateurs,
    _niveaux_refuses_par_prof,
    _profs_eligibles,
    _slack_minimal_par_prof,
    _suggestion_regroupement_classes,
)
from solver.diagnostic_core import diagnostiquer

PIED_INFAISABILITE = (
    "Avec ces pistes, le solveur a exploré toutes les combinaisons horaires "
    "possibles dans ce cadre — aucune ne satisfait l'ensemble des contraintes dures."
)


def _lignes_contraintes_dures(d: DonneesCollege) -> list[str]:
    lignes: list[str] = []
    for pref in d.preferences:
        if pref.priorite != "contrainte":
            continue
        p = d.profs.get(pref.prof_id)
        nom = p.nom_complet if p else pref.prof_id
        lignes.append(f"  • {nom} ({pref.prof_id}) — {pref.type} : {pref.valeur}")
    for de in d.distances_etab:
        paire = f"{de.etab_a}↔{de.etab_b}"
        if de.regroupement_demi == "contrainte":
            lignes.append(f"  • Distances {paire} : regroupement demi-journée (contrainte)")
        if de.regroupement_jour == "contrainte":
            lignes.append(f"  • Distances {paire} : regroupement journée entière (contrainte)")
    return lignes


def _besoins_liste(
    d: DonneesCollege,
    besoins: dict[tuple[str, str], int],
) -> list[tuple[str, str, int, list[str]]]:
    classes_by_id = {c.id: c for c in d.classes}
    niveaux_refuses = _niveaux_refuses_par_prof(d)
    out: list[tuple[str, str, int, list[str]]] = []
    for (c_id, m_id), h in besoins.items():
        c = classes_by_id[c_id]
        elig = _profs_eligibles(d, c, m_id, niveaux_refuses)
        out.append((c_id, m_id, h, elig))
    return out


def _lignes_profs_stress(
    d: DonneesCollege,
    besoins: dict[tuple[str, str], int] | None,
    affectation: dict[tuple[str, str], str] | None,
) -> list[str]:
    import math

    lignes: list[str] = []
    for row in diagnostiquer(d):
        if row["deficit_h"] <= 0:
            continue
        profs_noms = ", ".join(p["nom"] for p in row["profs"][:4])
        doubles = [
            f"{p['nom']} (+{','.join(p['partage'])})"
            for p in row["profs"] if p.get("partage")
        ][:3]
        ligne = (
            f"  • {row['matiere_nom']} ({row['matiere_id']}) : "
            f"besoin {row['besoin_h']}h / capacité {row['capacite_h']}h "
            f"(manque {row['deficit_h']}h)"
        )
        if profs_noms:
            ligne += f" — {profs_noms}"
        if doubles:
            ligne += f" ; élargir double matière : {', '.join(doubles)}"
        elif row["deficit_h"] > 0:
            ligne += (
                f" → embaucher ~{max(1, math.ceil(row['deficit_h'] / 18))} prof(s) "
                f"ou ajouter un Combleur"
            )
        lignes.append(ligne)

    if besoins:
        besoins_list = _besoins_liste(d, besoins)
        slack = _slack_minimal_par_prof(d, besoins_list)
        for p_id, s in slack[:8]:
            p = d.profs[p_id]
            mats = ", ".join(p.matieres)
            lignes.append(
                f"  • {p.nom_complet} ({p_id}) [{mats}] : +{s}h au-delà du contrat "
                f"({p.h_contrat}h) — augmenter le contrat ou élargir les matières"
            )
        hall = _hall_violateurs(d, besoins_list, max_groupes=4)
        for h in hall:
            if h.strip().startswith("("):
                continue
            lignes.append(h.replace("  →", "  •", 1))

    if besoins and affectation:
        for line in verifier_creneaux_profs_disponibles(d, affectation, besoins):
            lignes.append(f"  • {line}")

    return lignes


def message_infaisabilite_structure(
    d: DonneesCollege,
    *,
    affectation: dict[tuple[str, str], str] | None = None,
    besoins: dict[tuple[str, str], int] | None = None,
    cause: str | None = None,
    extra_profs: list[str] | None = None,
    salles_extra: str | None = None,
) -> str:
    """Message d'échec : cause, puis ① contraintes ② classes ③ profs, puis pied."""
    parts: list[str] = []
    if cause:
        parts.append(cause)

    contraintes = _lignes_contraintes_dures(d)
    parts.append(
        "① Contraintes (priorité « contrainte » — peuvent bloquer le solveur) :\n"
        + (
            "\n".join(contraintes)
            if contraintes
            else "  • Aucune préférence en contrainte dure — vérifier salles ou créneaux."
        )
    )

    classes = _suggestion_regroupement_classes(d)
    parts.append(
        "② Classes — augmenter, regrouper ou supprimer des divisions :\n"
        + (
            "\n".join(classes)
            if classes
            else "  • Effectifs homogènes — pas de scission/regroupement évident."
        )
    )

    if extra_profs:
        corps_profs = "\n".join(extra_profs)
    else:
        auto = _lignes_profs_stress(d, besoins, affectation)
        corps_profs = (
            "\n".join(auto)
            if auto
            else "  • Capacité horaire globalement suffisante par matière."
        )
    parts.append(
        "③ Professeurs — embaucher, augmenter un contrat ou double matière :\n"
        + corps_profs
    )

    if salles_extra:
        parts.append("Salles (complément) :\n" + salles_extra)

    parts.append(PIED_INFAISABILITE)
    return "\n\n".join(parts)


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
    """Message d'erreur CP-SAT structuré : contraintes → classes → profs."""
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

    cause: str | None = None
    salles_extra: str | None = None

    if not ok_profs:
        cause = (
            "Cause immédiate : grille horaire / simultanéité insuffisante.\n  • "
            + "\n  • ".join(diagnostiquer_creneaux_profs(d, affectation, besoins))
        )
    elif not ok_prefs:
        cause = (
            "Cause immédiate : préférences ou regroupements en contrainte dure "
            "incompatibles avec le programme."
        )
    elif not ok_salles:
        bloquant, _info = diagnostiquer_salles(d, besoins)
        cause = "Cause immédiate : salles saturées par créneau."
        salles_extra = "  • " + "\n  • ".join(
            bloquant or ["capacité salle insuffisante à l'horaire"]
        )
    else:
        cause = (
            "Cause immédiate : contraintes souples ou objectif trop strict "
            "(aucune grille ne satisfait toutes les pénalités)."
        )

    _bloquant, info_salles = diagnostiquer_salles(d, besoins)
    if info_salles and not salles_extra:
        salles_extra = "  ℹ " + "\n  ℹ ".join(info_salles)

    return message_infaisabilite_structure(
        d,
        affectation=affectation,
        besoins=besoins,
        cause=cause,
        salles_extra=salles_extra,
    )
