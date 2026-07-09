# Timetabler Solver

Serveur de calcul local pour [Timetabler](https://dnavatar.org/pages/timetabler/editor.html) — optimisation d'emplois du temps avec [OR-Tools](https://developers.google.com/optimization) (Google).

## Lancer avec Docker (recommandé)

```sh
docker run --rm -p 8002:8002 dnavatar/timetabler
```

### Arrêter Docker

- **Terminal où la commande tourne** : `Ctrl+C`
- **Depuis un autre terminal** (conteneur lancé en arrière-plan) :

```sh
docker stop $(docker ps -q --filter publish=8002)
```

(`--rm` supprime le conteneur à l'arrêt.)

## Lancer avec Python (via uv)

```sh
uv run uvicorn server:app --port 8002
```

### Arrêter uvicorn

- **Terminal où la commande tourne** : `Ctrl+C`
- **Depuis un autre terminal** (macOS / Linux) :

```sh
lsof -ti :8002 | xargs kill
```

## Licence

**Apache License 2.0 + Commons Clause** — voir [LICENSE](LICENSE).

- ✅ Libre de forker, modifier, adapter, distribuer — **usage non commercial uniquement**
- ❌ Usage commercial : licence requise auprès de DNAvatar.org (contact@dnavatar.org)

Copyright 2025-2026 DNAvatar.org — Arnaud Maignan
