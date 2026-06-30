# File: donnees.py - Dataclasses DonneesCollege et entités métier
# Desc: En français, dans l'architecture emploi du temps, je suis…
# Version 1.0.0
# Copyright 2025 DNAvatar.org - Arnaud Maignan
# Licensed under Apache License 2.0 with Commons Clause.
# See LICENSE_HEADER.txt for full terms.
# Date: June 27, 2025
# Logs:
# - Split from solve_ecoles.py monolith

from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class Classe:
    id: str
    nom: str
    niveau: str
    nb_eleves: int
    specificite: str | None
    etab_id: str = ""


@dataclass
class Matiere:
    id: str
    nom: str
    abrev: str
    salle_type: str | None  # None = salle standard
    duree_h: int            # 1 ou 2 (non utilisé dans le modèle v1)
    couleur: str = ""       # couleur hex bg, ex. "#4e79a7"


@dataclass
class ProgrammeItem:
    niveau: str           # "" si specificite_requis est renseigné
    matiere_id: str
    h_semaine: float
    notes: str | None
    specificite_requis: str | None = None  # colonne option dans la matrice programmes


@dataclass
class Prof:
    id: str
    nom_complet: str
    matieres: list[str]
    h_contrat: int
    jours_dispo: set[str]
    niveaux: set[str] = field(default_factory=set)  # vide = tous les niveaux


@dataclass
class Etablissement:
    id: str


@dataclass
class Salle:
    id: str
    nom: str
    type: str
    capacite: int
    etab_id: str = ""
    stabilite: str = "partagee"         # fixe | partagee


@dataclass
class Creneau:
    id: str
    jour: str
    debut: str
    fin: str
    type: str = "cours"   # cours | pause | dejeuner


@dataclass
class Preference:
    prof_id: str
    type: str      # jour_libre | debut_pas_avant | max_heures_consec | creneau_bloque | niveau_refuse | etab_requis
    valeur: str
    priorite: str  # haute | moyenne | basse | contrainte
    operateur: str = 'ET'  # ET (défaut) | OU → lie cette pref à la précédente du même prof


@dataclass
class DistanceEtab:
    etab_a: str
    etab_b: str
    dist_km: float
    min_transport: int
    regroupement_demi: str  # "" | "basse" | "moyenne" | "haute" | "contrainte"
    regroupement_jour: str  # "" | "basse" | "moyenne" | "haute" | "contrainte"


@dataclass
class DonneesCollege:
    classes: list[Classe] = field(default_factory=list)
    matieres: dict[str, Matiere] = field(default_factory=dict)
    programme: list[ProgrammeItem] = field(default_factory=list)
    profs: dict[str, Prof] = field(default_factory=dict)
    salles: list[Salle] = field(default_factory=list)
    etablissements: dict[str, Etablissement] = field(default_factory=dict)
    creneaux: list[Creneau] = field(default_factory=list)
    horaires: list[Creneau] = field(default_factory=list)  # tous types (cours, pause, déjeuner)
    ref: dict[str, str] = field(default_factory=dict)      # onglet referentiel « horaires »
    preferences: list[Preference] = field(default_factory=list)
    distances_etab: list[DistanceEtab] = field(default_factory=list)
