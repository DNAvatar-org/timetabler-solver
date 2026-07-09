# File: pre_assign.py - Pré-affectation profs (CP-SAT staffing global)
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
from collections import defaultdict
from itertools import combinations

from solver.combleur import _est_combleur
from solver.constants import LIBELLE_COMBLEUR, NUM_SEARCH_WORKERS, POIDS, POIDS_COMBLEUR, PRE_ASSIGN_META
from solver.donnees import DonneesCollege
from solver.programme import _niveau_match, matiere_applicable, nb_creneaux

def _niveaux_refuses_par_prof(d: DonneesCollege) -> dict[str, set[str]]:
    """Seulement priorité=contrainte : hard exclusion dans le greedy."""
    refus: dict[str, set[str]] = defaultdict(set)
    for pref in d.preferences:
        if pref.type == "niveau_refuse" and pref.priorite == "contrainte":
            refus[pref.prof_id].add(pref.valeur)
    return refus


def _profs_eligibles(
    d: DonneesCollege,
    classe: Classe,
    m_id: str,
    niveaux_refuses: dict[str, set[str]],
    sans_combleurs: bool = False,
) -> list[str]:
    out = [
        p.id for p in d.profs.values()
        if m_id in p.matieres
        and (not p.niveaux or classe.niveau in p.niveaux)
        and classe.niveau not in niveaux_refuses[p.id]
    ]
    if sans_combleurs:
        out = [p_id for p_id in out if not _est_combleur(d.profs[p_id])]
    return out


def _plafond_usuel_classe(niveau: str) -> int:
    """Plafond usuel d'élèves par classe (pas de maximum légal national dans
    le secondaire) : ~30 collège, ~35 lycée GT, 24 voie pro / CAP (ateliers)."""
    n = niveau or ""
    if "Pro" in n or "CAP" in n.upper():
        return 24
    if any(x in n for x in ("2nde", "1ère", "1ere", "Tle")):
        return 35
    return 30


def _suggestion_regroupement_classes(d: DonneesCollege) -> list[str]:
    """La vraie solution de terrain à un manque de profs : remplir les classes.
    Supprimer une division libère TOUTES ses heures de programme d'un coup —
    bien plus efficace qu'ajuster des contrats prof heure par heure.
    Retourne les niveaux où les élèves tiendraient dans moins de classes."""
    import math
    par_niveau: dict[str, list[int]] = defaultdict(list)
    for c in d.classes:
        if not _est_combleur_classe(c):
            par_niveau[c.niveau].append(c.nb_eleves or 0)
    suggestions = []
    for niveau, effectifs in sorted(par_niveau.items()):
        total = sum(effectifs)
        n = len(effectifs)
        if total <= 0 or n < 2:
            continue  # effectifs non renseignés ou rien à regrouper
        plafond = _plafond_usuel_classe(niveau)
        n_min = max(1, math.ceil(total / plafond))
        if n_min < n:
            suggestions.append(
                f"  • {niveau} : {n} classes (~{round(total / n)} élèves/classe) "
                f"→ {n_min} classes de ~{math.ceil(total / n_min)} "
                f"(plafond usuel {plafond}) libère {(n - n_min)} division(s)"
            )
    return suggestions


def _est_combleur_classe(c) -> bool:
    """Pas de notion de classe-combleur aujourd'hui — hook conservé lisible."""
    return False


def _bloc_suggestion_regroupement(d: DonneesCollege) -> list[str]:
    """Lignes à insérer EN PREMIÈRE suggestion des messages d'échec staffing."""
    sugg = _suggestion_regroupement_classes(d)
    if not sugg:
        return []
    return [
        "\n  → D'abord : REMPLIR les classes existantes plutôt que toucher aux",
        "  contrats — chaque division supprimée libère toutes ses heures :",
        *sugg,
    ]


def _erreur_staffing_impossible(
    d: DonneesCollege,
    hors_contrat: list[tuple[str, str, int, str]],
) -> str:
    classes_by_id = {c.id: c for c in d.classes}
    lignes = []
    for c_id, m_id, h, detail in hors_contrat:
        c = classes_by_id[c_id]
        mat = d.matieres[m_id].abrev if m_id in d.matieres else m_id
        lignes.append(
            f"  • {c.nom} ({c_id}), matière {mat} ({h}h) — {detail}"
        )
    lignes.extend(_bloc_suggestion_regroupement(d))
    return (
        f"{len(hors_contrat)} cours non assignable(s) dans les limites du contrat horaire :\n"
        + "\n".join(lignes)
    )


def _slack_minimal_par_prof(
    d: DonneesCollege,
    besoins: list[tuple[str, str, int, list[str]]],
) -> list[tuple[str, int]]:
    """Heures manquantes par prof (min. L1) pour rendre l'affectation faisable."""
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    assign: dict[tuple[str, str, str], cp_model.IntVar] = {}
    for c_id, m_id, _h, eligibles in besoins:
        vars_p = []
        for p_id in eligibles:
            v = model.new_bool_var(f"sl_{c_id}_{m_id}_{p_id}")
            assign[(c_id, m_id, p_id)] = v
            vars_p.append(v)
        model.add(sum(vars_p) == 1)

    slack_vars: dict[str, cp_model.IntVar] = {}
    charge_par_prof: dict[str, list] = defaultdict(list)
    for c_id, m_id, heures, eligibles in besoins:
        for p_id in eligibles:
            charge_par_prof[p_id].append(heures * assign[(c_id, m_id, p_id)])

    plafond = sum(h for _, _, h, _ in besoins)
    for p_id, terms in charge_par_prof.items():
        s = model.new_int_var(0, plafond, f"slack_{p_id}")
        slack_vars[p_id] = s
        model.add(sum(terms) <= d.profs[p_id].h_contrat + s)

    model.minimize(sum(slack_vars.values()))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30
    if NUM_SEARCH_WORKERS:
        solver.parameters.num_search_workers = NUM_SEARCH_WORKERS
    statut = solver.solve(model)
    if statut not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return []

    result = [
        (p_id, int(solver.value(s)))
        for p_id, s in slack_vars.items()
        if solver.value(s) > 0
    ]
    result.sort(key=lambda x: -x[1])
    return result


def _hall_violateurs(
    d: DonneesCollege,
    besoins: list[tuple[str, str, int, list[str]]],
    *,
    max_groupes: int = 5,
) -> list[str]:
    """
    Indices Hall sur profs mono-matière (sous-ensembles de taille 1–3).
    Souvent trompeur quand les profs sont polyvalents — à afficher avec parcimonie.
    """
    all_mats = sorted({m_id for _, m_id, _, _ in besoins})
    mats_du_prof: dict[str, set[str]] = defaultdict(set)
    for _, m_id, _, eligibles in besoins:
        for p_id in eligibles:
            mats_du_prof[p_id].add(m_id)

    candidats: list[tuple[int, int, str, str, int]] = []
    seen: set[frozenset] = set()

    for size in range(1, min(4, len(all_mats) + 1)):
        for mat_subset in combinations(sorted(all_mats), size):
            mat_set = frozenset(mat_subset)
            if mat_set in seen:
                continue
            profs_exclusifs = {
                p_id for p_id, mats in mats_du_prof.items()
                if mats <= mat_set
            }
            if not profs_exclusifs:
                continue
            demand = sum(h for _, m_id, h, _ in besoins if m_id in mat_set)
            cap = sum(d.profs[p].h_contrat for p in profs_exclusifs if p in d.profs)
            if demand > cap:
                seen.add(mat_set)
                mat_abrevs = "+".join(
                    d.matieres[m].abrev if m in d.matieres else m
                    for m in sorted(mat_subset)
                )
                prof_detail = ", ".join(
                    f"{d.profs[p].nom_complet} ({d.profs[p].h_contrat}h)"
                    for p in sorted(profs_exclusifs) if p in d.profs
                )
                candidats.append((
                    demand - cap, size, mat_abrevs, prof_detail, len(profs_exclusifs),
                ))

    candidats.sort(key=lambda x: (-x[0], x[1]))
    lignes: list[str] = [
        "  (Pools mono-matière uniquement — les profs enseignant plusieurs matières ne sont pas comptés.)",
    ]
    for deficit, _size, mat_abrevs, prof_detail, nb in candidats[:max_groupes]:
        lignes.append(
            f"  → [{mat_abrevs}] déficit indicatif {deficit}h "
            f"({nb} prof(s) mono-matière : {prof_detail})"
        )
    reste = len(candidats) - max_groupes
    if reste > 0:
        lignes.append(
            f"  … {reste} autre(s) groupe(s) mono-matière non affichés (souvent trompeurs)."
        )
    if len(lignes) == 1:
        return []
    return lignes


def _erreur_packing_contrats(
    d: DonneesCollege,
    besoins: list[tuple[str, str, int, list[str]]],
) -> str:
    """
    Échec de pré-affectation : chaque matière a assez de profs pris isolément,
    mais les contrats polyvalents ne suffisent pas globalement.
    """
    besoin_total = sum(h for _, _, h, _ in besoins)
    contrat_total = sum(p.h_contrat for p in d.profs.values())
    slack = _slack_minimal_par_prof(d, besoins)
    slack_total = sum(s for _, s in slack)

    lignes = [
        "Répartition impossible : contrats horaires saturés (profs enseignant plusieurs matières).",
        f"  Programme {besoin_total}h / contrats horaires {contrat_total}h "
        f"(écart nominal {contrat_total - besoin_total:+d}h).",
    ]
    if slack_total:
        lignes.append(
            f"  Manque estimé pour tout affecter : ~{slack_total}h réparties sur :"
        )
        for p_id, s in slack[:10]:
            p = d.profs[p_id]
            mats = ", ".join(p.matieres)
            lignes.append(
                f"  • {p.nom_complet} ({p_id}) [{mats}] : "
                f"contrat horaire {p.h_contrat}h, +{s}h estimées"
            )
        if len(slack) > 10:
            lignes.append(f"  • … et {len(slack) - 10} autre(s) prof(s)")

        # Identifier les cours réservés exclusivement aux profs saturés
        profs_satures = {p_id for p_id, _ in slack}
        classes_by_id = {c.id: c for c in d.classes}
        niveaux_refuses = _niveaux_refuses_par_prof(d)
        goulots: list[str] = []
        for c_id, m_id, heures, eligibles in besoins:
            if any(p not in profs_satures for p in eligibles):
                continue  # au moins un prof non-saturé peut le prendre
            classe = classes_by_id.get(c_id)
            mat = d.matieres[m_id] if m_id in d.matieres else None
            mat_abrev = mat.abrev if mat else m_id
            salle_note = f" [salle {mat.salle_type}]" if mat and mat.salle_type else ""
            c_nom = classe.nom if classe else c_id
            niv = classe.niveau if classe else "?"
            # Pourquoi les autres profs de la matière ne sont-ils pas éligibles ?
            exclus_raisons: list[str] = []
            for p in d.profs.values():
                if m_id not in p.matieres or p.id in eligibles:
                    continue
                if classe and p.niveaux and classe.niveau not in p.niveaux:
                    niv_str = "/".join(sorted(p.niveaux))
                    exclus_raisons.append(f"{p.nom_complet} → niveaux restreints ({niv_str})")
                elif classe and classe.niveau in niveaux_refuses.get(p.id, set()):
                    exclus_raisons.append(f"{p.nom_complet} → niveau_refuse contrainte")
                else:
                    exclus_raisons.append(f"{p.nom_complet} → non éligible (autre raison)")
            el_noms = ", ".join(d.profs[p].nom_complet for p in eligibles if p in d.profs)
            ligne = f"  ↳ {c_nom} ({niv}) · {mat_abrev}{salle_note} {heures}h — seul(s) éligible(s) : {el_noms}"
            if exclus_raisons:
                ligne += "\n      Exclus : " + " | ".join(exclus_raisons[:4])
            goulots.append(ligne)
        if goulots:
            lignes.append(
                f"\n  Cours sans remplaçant éligible ({len(goulots)}) "
                "— forcés sur le(s) prof(s) saturé(s) :"
            )
            lignes.extend(goulots[:15])
            if len(goulots) > 15:
                lignes.append(f"  … et {len(goulots) - 15} autre(s) cours")
        else:
            if slack_total:
                lignes.append(
                    "\n  Les cours ont des remplaçants éligibles par matière, mais "
                    "les contrats horaires des profs enseignant plusieurs matières ne suffisent pas "
                    f"globalement (~{slack_total}h manquantes, voir ci-dessus)."
                )
                lignes.extend(_bloc_suggestion_regroupement(d))
                lignes.append(
                    f"\n  → Sinon : augmenter le contrat horaire du/des prof(s) listé(s), "
                    f"ou ajouter un {LIBELLE_COMBLEUR} (~{max(slack_total, 12)}–25h) "
                    f"sur les matières en tension."
                )
            else:
                hall = _hall_violateurs(d, besoins, max_groupes=5)
                if hall:
                    lignes.append(
                        "\n  Goulot de flux (Hall) — indicatif, pools mono-matière seulement :"
                    )
                    lignes.extend(hall)
                    lignes.extend(_bloc_suggestion_regroupement(d))
                    lignes.append(
                        "\n  Sinon : augmenter le contrat horaire d'un prof du pool, "
                        f"ou ajouter un {LIBELLE_COMBLEUR}."
                    )
                else:
                    lignes.append(
                        "\n  Tous les cours ont des remplaçants éligibles et les pools de\n"
                        "  flux mono-matière sont équilibrés. Vérifiez etab_requis (contrainte)\n"
                        "  ou niveau_refuse qui pourraient créer un conflit caché."
                    )
    else:
        lignes.append(
            "  Vérifiez les préférences niveau_refuse (contrainte) et etab_requis (contrainte)."
        )
    return "\n".join(lignes)


def _pre_assigner_profs_essai(
    d: DonneesCollege,
    sans_combleurs: bool,
    *,
    erreur_detaillee: bool,
) -> dict[tuple[str, str], str] | None:
    """
    Une passe CP-SAT de pré-affectation.
    sans_combleurs=True : pool sans profs Combleur ; None si infaisable.
    """
    from ortools.sat.python import cp_model

    niveaux_refuses = _niveaux_refuses_par_prof(d)
    besoins: list[tuple[str, str, int, list[str]]] = []
    hors_contrat: list[tuple[str, str, int, str]] = []

    for classe in sorted(d.classes, key=lambda c: c.id):
        for prog in d.programme:
            if not _niveau_match(prog, classe):
                continue
            m_id = prog.matiere_id
            if m_id not in d.matieres:
                continue
            if not matiere_applicable(prog, classe, d.matieres):
                continue
            heures = nb_creneaux(prog.h_semaine)
            eligibles = _profs_eligibles(
                d, classe, m_id, niveaux_refuses, sans_combleurs=sans_combleurs,
            )
            if not eligibles:
                mat = d.matieres[m_id].abrev
                detail = f"aucun prof {mat} éligible pour le niveau {classe.niveau}"
                if sans_combleurs:
                    tous = _profs_eligibles(d, classe, m_id, niveaux_refuses)
                    if tous:
                        detail += " (Combleurs seuls disponibles)"
                hors_contrat.append((classe.id, m_id, heures, detail))
                continue
            besoins.append((classe.id, m_id, heures, eligibles))

    if hors_contrat:
        if sans_combleurs:
            return None
        raise ValueError(_erreur_staffing_impossible(d, hors_contrat))

    model = cp_model.CpModel()
    assign: dict[tuple[str, str, str], cp_model.IntVar] = {}

    for c_id, m_id, _h, eligibles in besoins:
        vars_p = []
        for p_id in eligibles:
            v = model.new_bool_var(f"aff_{c_id}_{m_id}_{p_id}")
            assign[(c_id, m_id, p_id)] = v
            vars_p.append(v)
        model.add(sum(vars_p) == 1)

    charge_par_prof: dict[str, list] = defaultdict(list)
    for c_id, m_id, heures, eligibles in besoins:
        for p_id in eligibles:
            charge_par_prof[p_id].append(heures * assign[(c_id, m_id, p_id)])

    for p_id, terms in charge_par_prof.items():
        model.add(sum(terms) <= d.profs[p_id].h_contrat)

    # Équilibrer la charge (secondaire) — faisable d'abord
    loads = []
    for p_id in charge_par_prof:
        t = model.new_int_var(0, d.profs[p_id].h_contrat, f"load_{p_id}")
        model.add(t == sum(charge_par_prof[p_id]))
        loads.append(t)

    # Pénaliser les affectations qui violent un niveau_refuse non-contrainte
    classes_by_id = {c.id: c for c in d.classes}
    niveaux_refuses_soft: dict[str, dict[str, int]] = defaultdict(dict)
    for pref in d.preferences:
        if pref.type == "niveau_refuse" and pref.priorite != "contrainte":
            p_nv = niveaux_refuses_soft[pref.prof_id]
            p_nv[pref.valeur] = max(p_nv.get(pref.valeur, 0), POIDS.get(pref.priorite, 1))

    penalites_nv = []
    for c_id, m_id, heures, eligibles in besoins:
        classe = classes_by_id[c_id]
        for p_id in eligibles:
            p_refus = niveaux_refuses_soft.get(p_id, {})
            if classe.niveau in p_refus:
                v = assign[(c_id, m_id, p_id)]
                penalites_nv.append(p_refus[classe.niveau] * heures * v)

    for pref in d.preferences:
        if pref.type != "etab_requis":
            continue
        p_id = pref.prof_id
        if p_id not in d.profs:
            continue
        req_etab = pref.valeur
        vars_etab = [
            assign[(c_id, m_id, p_id)]
            for (c_id, m_id, _h, eligibles) in besoins
            if p_id in eligibles
            and classes_by_id.get(c_id)
            and classes_by_id[c_id].etab_id == req_etab
        ]
        if not vars_etab:
            print(f"  ⚠ etab_requis {p_id}→{req_etab} : aucune classe éligible à cet établissement")
            continue
        if pref.priorite == "contrainte":
            model.add(sum(vars_etab) >= 1)
        else:
            poids_e = POIDS.get(pref.priorite, 1)
            at_etab = model.new_bool_var(f"at_etab_{p_id}_{req_etab}")
            model.add(sum(vars_etab) >= 1).only_enforce_if(at_etab)
            model.add(sum(vars_etab) == 0).only_enforce_if(at_etab.Not())
            not_at_etab = model.new_bool_var(f"not_etab_{p_id}_{req_etab}")
            model.add(at_etab + not_at_etab == 1)
            penalites_nv.append(poids_e * not_at_etab)

    penalites_combleur = []
    for c_id, m_id, heures, eligibles in besoins:
        for p_id in eligibles:
            if _est_combleur(d.profs[p_id]):
                penalites_combleur.append(
                    POIDS_COMBLEUR * heures * assign[(c_id, m_id, p_id)]
                )

    # sum(loads) = constante (besoin total) — aucun effet utile.
    # On minimise plutôt le maximum de contrat non-utilisé pour équilibrer la charge.
    if loads:
        max_h = max(d.profs[p].h_contrat for p in charge_par_prof)
        max_slack = model.new_int_var(0, max_h, "max_slack")
        for p_id, t in zip(charge_par_prof, loads):
            model.add(d.profs[p_id].h_contrat - t <= max_slack)
        obj = max_slack
        if penalites_combleur:
            obj = obj + sum(penalites_combleur)
        if penalites_nv:
            obj = obj + sum(penalites_nv)
        model.minimize(obj)
    elif penalites_combleur:
        obj = sum(penalites_combleur)
        if penalites_nv:
            obj = obj + sum(penalites_nv)
        model.minimize(obj)
    elif penalites_nv:
        model.minimize(sum(penalites_nv))

    solver = cp_model.CpSolver()
    if NUM_SEARCH_WORKERS:
        solver.parameters.num_search_workers = NUM_SEARCH_WORKERS
    statut = solver.solve(model)

    if statut not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if sans_combleurs:
            return None
        # Aide au diagnostic : besoin vs capacité par matière
        for c_id, m_id, heures, eligibles in besoins:
            cap = sum(d.profs[p].h_contrat for p in eligibles)
            besoin_matiere = sum(
                h for cid, mid, h, _ in besoins if mid == m_id
            )
            if besoin_matiere > cap:
                mat = d.matieres[m_id].abrev
                hors_contrat.append((
                    c_id, m_id, heures,
                    f"capacité {mat} insuffisante ({besoin_matiere}h demandées "
                    f"vs {cap}h de contrats horaires profs éligibles)",
                ))
                break
        if not hors_contrat:
            raise ValueError(_erreur_packing_contrats(d, besoins))
        raise ValueError(_erreur_staffing_impossible(d, hors_contrat))

    affectation: dict[tuple[str, str], str] = {}
    charge: dict[str, int] = defaultdict(int)
    for c_id, m_id, heures, eligibles in besoins:
        for p_id in eligibles:
            if solver.value(assign[(c_id, m_id, p_id)]) == 1:
                affectation[(c_id, m_id)] = p_id
                charge[p_id] += heures
                break

    if erreur_detaillee:
        print("\n  Charge des profs (vs contrat) :")
        for p_id in sorted(d.profs):
            c = charge[p_id]
            if c == 0:
                continue
            prof = d.profs[p_id]
            flag = " ⚠ DÉPASSEMENT" if c > prof.h_contrat else ""
            tag = " [Combleur]" if _est_combleur(prof) else ""
            print(f"    {prof.nom_complet:<25} {c:>3}h / {prof.h_contrat}h{flag}{tag}")

    return affectation


def pre_assigner_profs(d: DonneesCollege) -> dict[tuple[str, str], str]:
    """
    Retourne {(classe_id, matiere_id): prof_id}.
    Ordre de remplissage : passe 1 sans Combleurs, passe 2 avec repli si infaisable.
    """
    affectation = _pre_assigner_profs_essai(
        d, sans_combleurs=True, erreur_detaillee=True,
    )
    if affectation is not None:
        print("  Pré-affectation : profs réels prioritaires (Combleurs exclus)")
        PRE_ASSIGN_META.clear()
        PRE_ASSIGN_META.update({
            "sans_combleur_ok": True,
            "combleurs_utilises": [],
        })
        return affectation

    print("  ⚠ Pré-affectation sans Combleur impossible — repli avec Combleurs")
    affectation = _pre_assigner_profs_essai(
        d, sans_combleurs=False, erreur_detaillee=True,
    )
    if affectation is None:
        PRE_ASSIGN_META.clear()
        PRE_ASSIGN_META.update({
            "sans_combleur_ok": False, "combleurs_utilises": [],
        })
        raise ValueError("Pré-affectation impossible (même avec Combleurs).")
    combleurs_utilises = sorted({
        p_id for p_id in affectation.values() if _est_combleur(d.profs[p_id])
    })
    PRE_ASSIGN_META.clear()
    PRE_ASSIGN_META.update({
        "sans_combleur_ok": False,
        "combleurs_utilises": combleurs_utilises,
    })
    return affectation
