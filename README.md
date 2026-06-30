# Timetabler Solver

Serveur de calcul local pour [Timetabler](https://dnavatar.org/pages/timetabler/editor.html) — optimisation d'emplois du temps avec [OR-Tools](https://developers.google.com/optimization) (Google).

## Lancer avec Docker (recommandé)

```sh
docker run --rm -p 8002:8002 dnavatar/timetabler
```

## Lancer avec Python (via uv)

```sh
uv run uvicorn server:app --port 8002
```

## Licence

Usage non commercial libre. Usage commercial : voir [LICENSE](LICENSE).
