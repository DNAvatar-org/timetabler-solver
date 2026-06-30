# File: affichage.py - Affichage console des résultats
# Desc: En français, dans l'architecture emploi du temps, je suis…
# Version 1.0.0
# Copyright 2025 DNAvatar.org - Arnaud Maignan
# Licensed under Apache License 2.0 with Commons Clause.
# See LICENSE_HEADER.txt for full terms.
# Date: June 27, 2025
# Logs:
# - Split from solve_ecoles.py monolith

from __future__ import annotations

from solver.constants import JOURS
from solver.donnees import DonneesCollege
from solver.salles import capacite_par_type

def afficher_emploi_du_temps(solution: dict, d: DonneesCollege) -> None:
    t_map = {cr.id: cr for cr in d.creneaux}

    print("\n" + "═" * 70)
    print("  EMPLOIS DU TEMPS — CLASSES")
    print("═" * 70)

    for classe in d.classes:
        cours = solution.get(classe.id, {})
        print(f"\n┌─ {classe.nom} ({classe.niveau})"
              f"  {classe.nb_eleves} élèves"
              + (f"  [{classe.specificite}]" if classe.specificite else ""))
        for jour in JOURS:
            slots = [
                (t_id, m_id, p_id)
                for t_id, (m_id, p_id) in cours.items()
                if t_map[t_id].jour == jour
            ]
            if not slots:
                continue
            slots.sort(key=lambda s: t_map[s[0]].debut)
            print(f"│  {jour.capitalize():<10}", end="")
            for t_id, m_id, p_id in slots:
                t = t_map[t_id]
                abrev = d.matieres[m_id].abrev if m_id in d.matieres else m_id
                print(f"  {t.debut}-{t.fin} {abrev:<5}", end="")
            print()
        nb_places = sum(1 for t in solution.get(classe.id, {}).values())
        print(f"└─ {nb_places} cours / semaine")


def afficher_emploi_du_temps_profs(solution: dict, d: DonneesCollege) -> None:
    t_map = {cr.id: cr for cr in d.creneaux}

    # Inverser : prof_id → {t_id: (c_id, m_id)}
    planning_profs: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    for c_id, cours in solution.items():
        for t_id, (m_id, p_id) in cours.items():
            planning_profs[p_id][t_id] = (c_id, m_id)

    print("\n" + "═" * 70)
    print("  EMPLOIS DU TEMPS — PROFESSEURS")
    print("═" * 70)

    for p_id in sorted(planning_profs):
        prof = d.profs[p_id]
        cours = planning_profs[p_id]
        total = len(cours)
        print(f"\n┌─ {prof.nom_complet}  ({total}h/semaine)")
        for jour in JOURS:
            slots = [
                (t_id, c_id, m_id)
                for t_id, (c_id, m_id) in cours.items()
                if t_map[t_id].jour == jour
            ]
            if not slots:
                continue
            slots.sort(key=lambda s: t_map[s[0]].debut)
            print(f"│  {jour.capitalize():<10}", end="")
            for t_id, c_id, m_id in slots:
                t = t_map[t_id]
                classe = next((c for c in d.classes if c.id == c_id), None)
                c_nom = classe.nom if classe else c_id
                abrev = d.matieres[m_id].abrev if m_id in d.matieres else m_id
                print(f"  {t.debut} {abrev}/{c_nom}", end="")
            print()
        print("└" + "─" * 40)


def afficher_satisfaction_preferences(solution: dict, d: DonneesCollege) -> None:
    t_map = {cr.id: cr for cr in d.creneaux}

    planning_profs: dict[str, dict[str, str]] = defaultdict(dict)
    for c_id, cours in solution.items():
        for t_id, (m_id, p_id) in cours.items():
            planning_profs[p_id][t_id] = t_map[t_id].jour

    print("\n" + "═" * 70)
    print("  SATISFACTION DES PRÉFÉRENCES")
    print("═" * 70)

    ok = ko = 0
    for pref in d.preferences:
        if pref.type not in ("jour_libre", "debut_pas_avant"):
            continue
        prof = d.profs.get(pref.prof_id)
        if not prof:
            continue
        cours_prof = planning_profs.get(pref.prof_id, {})

        if pref.type == "jour_libre":
            violation = any(jour == pref.valeur for jour in cours_prof.values())
        elif pref.type == "debut_pas_avant":
            violation = any(
                t_map[t_id].debut < pref.valeur
                for t_id in cours_prof
            )
        else:
            continue

        statut = "✗ VIOLÉE" if violation else "✓"
        if pref.priorite == "haute":
            statut += " [HAUTE]" if violation else " [haute]"
        print(f"  {statut:<18} {prof.nom_complet:<22} "
              f"{pref.type}={pref.valeur}  ({pref.priorite})")
        if violation:
            ko += 1
        else:
            ok += 1

    total = ok + ko
    print(f"\n  Satisfaites : {ok}/{total}   Violées : {ko}/{total}")


# ---------------------------------------------------------------------------
# Résumé des données chargées
# ---------------------------------------------------------------------------

def afficher_resume(d: DonneesCollege) -> None:
    print("═" * 60)
    print("  Collège Jeanne d'Arc — Paray-le-Monial (71600)")
    print("  Ensemble Scolaire La Salle — UAI 0711314T")
    print("═" * 60)
    niveaux: list[str] = []
    for c in d.classes:                       # ordre d'apparition dans le xlsx
        if c.niveau not in niveaux:
            niveaux.append(c.niveau)
    for niv in niveaux:
        cls = [c for c in d.classes if c.niveau == niv]
        print(f"  {niv} : {len(cls)} classes, "
              f"{sum(c.nb_eleves for c in cls)} élèves")
    print(f"\n  Total élèves  : {sum(c.nb_eleves for c in d.classes)}")
    print(f"  Profs         : {len(d.profs)}")
    print(f"  Matières      : {len(d.matieres)}")
    print(f"  Salles        : {len(d.salles)}")
    print(f"  Créneaux cours: {len(d.creneaux)} / semaine")
    cap = capacite_par_type(d.salles)
    print(f"  Salles spéc.  : labo-sciences×{cap.get('labo-sciences',0)}"
          f"  labo-langues×{cap.get('labo-langues',0)}"
          f"  gymnase×{cap.get('gymnase',0)}")
    print(f"  Préférences   : {len(d.preferences)} "
          f"({sum(1 for p in d.preferences if p.priorite=='haute')} haute priorité)")
    print("═" * 60)
