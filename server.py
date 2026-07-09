"""
TimeTabler — API générique OR-Tools.
Reçoit les données en JSON, retourne le planning en HTML.

Lancement (M4) :
    uv run uvicorn server:app --host 0.0.0.0 --port 8002
"""

from __future__ import annotations
import asyncio
import json
import math
import os
import sys
import time
from io import StringIO
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

try:
    from progress import reset_progress as _reset_progress, progress_stream as _progress_stream
except ImportError:
    def _reset_progress() -> None: pass  # noqa: E704
    async def _progress_stream(): yield ":\n\n"  # type: ignore[misc]

# Chemins
HERE   = Path(__file__).parent
STATIC = HERE / "static"

# SHA256 token list — tokens_sha256.txt (une ligne = un hash, # = commentaire)
# Env var TIMETABLER_TOKENS = hashes comma-séparés (pour Docker/CI)
_TOKENS_FILE = HERE / "tokens_sha256.txt"
_ALLOWED: set[str] = set()

# TOFU — liaison code ↔ machine (anti-partage). { token_hash: {mid, fp, ip, ts} }
# Au 1er usage réussi, le code se lie à la machine ; ensuite refusé ailleurs.
# Pour "libérer" un code (changement de machine) : retirer sa ligne du JSON.
_BINDINGS_FILE = HERE / "bindings.json"
_bindings: dict[str, dict] = {}


def _load_bindings() -> None:
    global _bindings
    if _BINDINGS_FILE.exists():
        try:
            _bindings = json.loads(_BINDINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            _bindings = {}


def _save_bindings() -> None:
    try:
        _BINDINGS_FILE.write_text(
            json.dumps(_bindings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


_load_bindings()

_solve_busy = False  # True pendant qu'un CP-SAT tourne


def _pop_demo_name(payload: dict) -> str | None:
    """Extrait demo_name du body JSON (ex. demo_paris) sans passer à charger_donnees_dict.

    Le classeur voyage dans le body ; demo_name active seulement l'écriture
    static/demo_*.{html,json} après solve/diagnostic (M4 ou localhost).
    """
    name = payload.pop("demo_name", None)
    if name is None:
        return None
    name = str(name).strip()
    if not name.startswith("demo_"):
        raise ValueError(f"demo_name invalide : {name!r}")
    STATIC.mkdir(parents=True, exist_ok=True)
    return name


def _write_demo_stats(name: str, score: float, temps: float, propagations: int, statut: str) -> str:
    path = STATIC / f"{name}_stats.json"
    path.write_text(
        json.dumps({
            "score": score,
            "temps": round(float(temps), 1),
            "propagations": int(propagations),
            "statut": statut,
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return path.name


def _write_demo_result_html(name: str, html: str) -> str:
    path = STATIC / f"{name}_result.html"
    path.write_text(html, encoding="utf-8")
    return path.name


def _write_demo_diag(name: str, diag: dict) -> str:
    path = STATIC / f"{name}_diag.json"
    path.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.name


def _save_demo_solve(
    name: str,
    html: str,
    score: float,
    temps: float,
    propagations: int,
    statut: str,
    d,
) -> list[str]:
    """Écrit static/demo_*.{html,stats.json,diag.json} sur le serveur (M4)."""
    import solve_ecoles
    diag = solve_ecoles.serialiser_diagnostic(d)
    return [
        _write_demo_result_html(name, html),
        _write_demo_stats(name, score, temps, propagations, statut),
        _write_demo_diag(name, diag),
    ]


_TOKENS_MTIME = -1.0


def _load_tokens() -> None:
    _ALLOWED.clear()
    if _TOKENS_FILE.exists():
        for line in _TOKENS_FILE.read_text(encoding="utf-8").splitlines():
            t = line.split("#")[0].strip().lower()
            if t:
                _ALLOWED.add(t)
    for t in os.environ.get("TIMETABLER_TOKENS", "").split(","):
        if t.strip():
            _ALLOWED.add(t.strip().lower())


def _tokens_maybe_reload() -> None:
    """Relit tokens_sha256.txt à la volée si sa date de modif a changé.
    Un code ajouté est actif immédiatement, sans redémarrage du serveur."""
    global _TOKENS_MTIME
    try:
        mtime = _TOKENS_FILE.stat().st_mtime if _TOKENS_FILE.exists() else 0.0
    except OSError:
        return
    if mtime != _TOKENS_MTIME:
        _TOKENS_MTIME = mtime
        _load_tokens()


_tokens_maybe_reload()

ALLOWED_ORIGINS = [
    "https://dnavatar.org",
    "https://www.dnavatar.org",
    "http://localhost:8001",
    "http://localhost:8002",
    "http://127.0.0.1:8001",
    "http://127.0.0.1:8002",
    "http://localhost",
    "http://127.0.0.1",
]


_NO_AUTH = bool(os.environ.get("TIMETABLER_NO_AUTH", ""))


_PUBLIC_PATHS = {"/api/health", "/api/trajet", "/api/resolve-gmaps", "/api/solve", "/api/diagnostic"}


async def check_auth(request: Request) -> None:
    if _NO_AUTH or request.url.path in _PUBLIC_PATHS:
        return
    cf_ip = request.headers.get("CF-Connecting-IP", "")
    host  = (request.client.host if request.client else "") or ""
    # Accès direct localhost (Tailscale / SSH local) sans en-tête CF → libre
    if not cf_ip and host in ("127.0.0.1", "::1"):
        return
    # Tout le reste (tunnel Cloudflare ou accès externe) → token obligatoire
    _tokens_maybe_reload()   # tokens_sha256.txt relu à la volée si modifié
    if not _ALLOWED:
        raise HTTPException(status_code=401, detail="Token requis — aucun token configuré sur le serveur")
    auth = request.headers.get("Authorization", "")
    token = auth[7:].lower() if auth.startswith("Bearer ") else ""
    if not token or token not in _ALLOWED:
        raise HTTPException(status_code=401, detail="Token invalide")

    # TOFU — le code se lie à la 1re machine qui l'utilise, refusé sur les autres.
    mid = request.headers.get("X-Machine-Id", "").strip()
    if not mid:
        raise HTTPException(status_code=403,
                            detail="Identifiant machine manquant — rafraîchissez la page.")
    bound = _bindings.get(token)
    if bound is None:
        _bindings[token] = {
            "mid": mid,
            "fp": request.headers.get("X-Machine-Fp", "").strip(),
            "ip": cf_ip,
            "ts": int(time.time()),
        }
        _save_bindings()
    elif bound.get("mid") != mid:
        raise HTTPException(status_code=403,
                            detail="Ce code est déjà lié à une autre machine.")


app = FastAPI(
    title="OR-Tools — TimeTabler API",
    version="1.0",
    dependencies=[Depends(check_auth)],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Géolocalisation — trajet domicile → collège
# ---------------------------------------------------------------------------

_GEO_BY_DEPT: dict[str, dict[str, tuple[float, float]]] = {}
_PARAY = (46.452128, 4.12068)


def _fetch_communes_api(codes_dept: list[str]) -> dict[str, tuple[float, float]]:
    """Récupère communes + coordonnées depuis geo.api.gouv.fr."""
    from urllib.request import urlopen as _urlopen
    geo: dict[str, tuple[float, float]] = {}
    for dep in codes_dept:
        url = (
            f"https://geo.api.gouv.fr/communes"
            f"?codeDepartement={dep}&fields=nom,centre&format=json"
        )
        try:
            with _urlopen(url, timeout=8) as r:
                data = json.loads(r.read().decode())
            for c in data:
                nom = c.get("nom", "").strip()
                coords = c.get("centre", {}).get("coordinates", [])
                if nom and len(coords) == 2:
                    lon, lat = coords   # GeoJSON → [lon, lat]
                    geo[nom] = (float(lat), float(lon))
            print(f"  Communes dept {dep} : {len(data)} chargées (API)")
        except Exception as e:
            print(f"  ⚠ Communes dept {dep} : API indisponible ({e})")
    return geo


def _communes_for_depts(codes_dept: list[str]) -> dict[str, tuple[float, float]]:
    """Communes par code dept — cache par dept, alimenté via geo.api.gouv.fr."""
    geo: dict[str, tuple[float, float]] = {}
    for dep in codes_dept:
        dep = dep.strip()
        if not dep.isdigit():
            continue
        if dep not in _GEO_BY_DEPT:
            _GEO_BY_DEPT[dep] = _fetch_communes_api([dep])
        geo.update(_GEO_BY_DEPT[dep])
    return geo

if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse((STATIC / "editor.html").read_text(encoding="utf-8"))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def _estimer_trajet_min(dist_km: float) -> int:
    if dist_km < 1:
        return 5
    if dist_km < 15:
        v = 30
    elif dist_km < 30:
        v = 35
    elif dist_km < 50:
        v = 55
    else:
        v = 65
    return max(5, round(60 * dist_km / v / 5) * 5)



@app.get("/api/trajet")
def get_trajet(commune: str = Query(..., description="Nom exact de la commune")):
    c = None
    for geo in _GEO_BY_DEPT.values():
        if commune in geo:
            c = geo[commune]
            break
    if c is None:
        raise HTTPException(404, f"Commune '{commune}' non trouvée — charger les communes via /api/communes?dept=…")
    dist = _haversine_km(*_PARAY, *c)
    return {"commune": commune, "dist_km": round(dist, 1), "minutes": _estimer_trajet_min(dist)}


@app.get("/api/resolve-gmaps")
def resolve_gmaps(url: str = Query(..., description="Lien Google Maps (maps.app.goo.gl ou maps.google.com)")):
    """Résout un lien Google Maps partagé et extrait les coordonnées GPS."""
    import re
    from urllib.request import urlopen as _urlopen, Request as _Req
    HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TimeTabler/1.0)"}
    try:
        req = _Req(url, headers=HEADERS)
        resp = _urlopen(req, timeout=8)
        final_url = resp.url
    except Exception as e:
        raise HTTPException(400, f"Impossible de résoudre le lien : {e}")

    # Format 1 : /@lat,lng,zoom  (le plus commun)
    m = re.search(r"/@(-?\d+\.\d+),(-?\d+\.\d+)", final_url)
    if m:
        return {"lat": float(m.group(1)), "lng": float(m.group(2)), "url": final_url}

    # Format 2 : ?q=lat,lng  (lien direct de type maps.google.com?q=...)
    m = re.search(r"[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)", final_url)
    if m:
        return {"lat": float(m.group(1)), "lng": float(m.group(2)), "url": final_url}

    # Format 3 : ll=lat,lng dans les anciens liens
    m = re.search(r"[?&]ll=(-?\d+\.\d+),(-?\d+\.\d+)", final_url)
    if m:
        return {"lat": float(m.group(1)), "lng": float(m.group(2)), "url": final_url}

    raise HTTPException(422, f"Coordonnées non trouvées dans l'URL finale : {final_url}")



# ---------------------------------------------------------------------------
# Routes API
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    try:
        import ortools  # noqa: F401
        ortools_ok = True
    except ImportError:
        ortools_ok = False
    return {"ok": True, "ortools": ortools_ok}


@app.get("/api/verify")
async def verify_code():
    # Route protégée (hors _PUBLIC_PATHS) : si on arrive ici, check_auth a validé
    # le token ET la liaison machine (TOFU). Sert au popup pour valider un code.
    return {"ok": True}


@app.post("/api/diagnostic")
async def run_diagnostic(request: Request):
    sys.path.insert(0, str(HERE))
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Body JSON invalide"}, status_code=400)
    try:
        demo_name = _pop_demo_name(payload)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    try:
        import importlib, solve_ecoles
        importlib.reload(solve_ecoles)
        d      = solve_ecoles.charger_donnees_dict(payload)
        diag   = solve_ecoles.serialiser_diagnostic(d)
        out = {
            "ok": diag["ok"],
            "nb_conflits": diag["nb_conflits"],
            "lignes": diag["lignes"],
            "charge_profs": diag["charge_profs"],
            "pre_assign_sans_combleur": diag["pre_assign_sans_combleur"],
            "nb_combleurs_actifs": diag["nb_combleurs_actifs"],
            "conseil_combleur": diag.get("conseil_combleur"),
        }
        if demo_name:
            out["demo_saved"] = [_write_demo_diag(demo_name, {
                "ok": out["ok"],
                "nb_conflits": out["nb_conflits"],
                "lignes": out["lignes"],
                "charge_profs": out["charge_profs"],
            })]
        return out
    except Exception as e:
        sys.stdout = sys.__stdout__
        raise HTTPException(500, str(e))


@app.post("/api/solve")
async def run_solver(request: Request):
    """
    Lance le solveur sur les données JSON envoyées par le client.
    Body : {classes, matieres, programmes, professeurs, salles,
            creneaux, referentiel, preferences}  — format {columns, rows}.
    """
    global _solve_busy

    if _solve_busy:
        return JSONResponse({
            "ok": False, "busy": True,
            "error": "Serveur occupé — une résolution OR-Tools est déjà en cours (~60 s). "
                     "Réessayez dans un moment, ou installez OR-Tools en local.",
        }, status_code=503)

    sys.path.insert(0, str(HERE))
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Body JSON invalide"}, status_code=400)

    demo_name = None
    try:
        demo_name = _pop_demo_name(payload)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    _reset_progress()
    _solve_busy = True
    try:
        import importlib
        import solve_ecoles
        import export_html
        importlib.reload(solve_ecoles)
        importlib.reload(export_html)

        d = solve_ecoles.charger_donnees_dict(payload)

        # CP-SAT dans un thread pour que l'event loop reste libre (→ 503 si requête concurrente)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, solve_ecoles.resoudre, d)

        if not result:
            return JSONResponse({"ok": False, "error": "Aucune solution trouvée."}, status_code=200)

        solution, score, temps, propagations, statut = result

        from export_html import generer_html, pref_satisfaction_dict
        import tempfile, pathlib
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
            tmp_path = pathlib.Path(tmp.name)
        try:
            generer_html(solution, d, tmp_path, score=score, temps=temps, propagations=propagations)
        except Exception as e:
            raise ValueError(
                f"OR-Tools a trouvé un planning (imperfections {int(score)}, {temps:.1f}s) "
                f"mais la génération HTML a échoué : {e}"
            ) from e
        html     = tmp_path.read_text(encoding="utf-8")
        tmp_path.unlink(missing_ok=True)
        pref_sat = pref_satisfaction_dict(solution, d)

        prof_heures = solve_ecoles.heures_par_prof(solution, d)
        resp = {"ok": True, "score": score, "temps": round(temps, 3),
                "propagations": propagations, "statut": statut, "html": html,
                "pref_satisfaction": pref_sat, "prof_heures": prof_heures}
        if demo_name:
            resp["demo_saved"] = _save_demo_solve(
                demo_name, html, score, temps, propagations, statut, d,
            )
        return resp

    except ValueError as e:
        import solve_ecoles
        err = str(e)
        conseil = solve_ecoles.conseil_combleur_pour_erreur(d, err)
        body: dict = {"ok": False, "error": err}
        if conseil:
            body["conseil_combleur"] = conseil
        return JSONResponse(body, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    finally:
        _solve_busy = False


@app.get("/api/progress")
async def api_progress():
    """SSE — progression live des phases CP-SAT pendant /api/solve."""
    return StreamingResponse(
        _progress_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/stop")
async def stop_solver():
    """Interrompt la résolution CP-SAT en cours.

    CP-SAT rend la meilleure solution trouvée jusque-là (planning partiel),
    ou une erreur « interrompue » si aucune solution n'a encore été trouvée.
    """
    sys.path.insert(0, str(HERE))
    try:
        import solve_ecoles  # déjà rechargé par /api/solve — même objet module
        stopped = solve_ecoles.stop_active_solver()
        return {"ok": True, "stopped": stopped}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# Mount root AFTER all API routes so /api/* take precedence
if STATIC.exists():
    app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static-root")

# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"\n  OR-Tools server → http://localhost:8002")
    print(f"  Docs API        → http://localhost:8002/docs\n")
    uvicorn.run("server:app", host="0.0.0.0", port=8002, reload=True,
                app_dir=str(HERE))
