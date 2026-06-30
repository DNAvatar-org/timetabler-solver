"""
Progression SSE du solveur CP-SAT.

  solve_ecoles.py  →  push_progress(data)
  server.py        →  /api/progress  (StreamingResponse + progress_stream())
                   →  reset_progress() avant chaque solve
"""
from __future__ import annotations

import asyncio
import json
import queue as _q_mod
from typing import AsyncGenerator

_queue: _q_mod.Queue[dict | None] = _q_mod.Queue()


def push_progress(data: dict) -> None:
    """Appelé depuis le thread solveur (thread-safe)."""
    _queue.put(data)


def reset_progress() -> None:
    """Vider la queue avant un nouveau calcul."""
    while not _queue.empty():
        try:
            _queue.get_nowait()
        except _q_mod.Empty:
            break


async def progress_stream() -> AsyncGenerator[str, None]:
    """Générateur async SSE — branché sur StreamingResponse dans server.py."""
    while True:
        try:
            item = _queue.get_nowait()
        except _q_mod.Empty:
            await asyncio.sleep(0.1)
            yield ":\n\n"  # keep-alive SSE (commentaire invisible côté client)
            continue
        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        if item is None or item.get("phase") == "done":
            break


# Callback CP-SAT — émis à chaque solution intermédiaire trouvée pendant solver.solve()
try:
    from ortools.sat.python import cp_model as _cp

    class SolveProgressCallback(_cp.CpSolverSolutionCallback):
        def on_solution_callback(self) -> None:
            push_progress({
                "phase": "solve",
                "obj":   int(self.objective_value),
                "bound": int(self.best_objective_bound),
                "t":     round(self.wall_time, 1),
            })

except ImportError:
    class SolveProgressCallback:  # type: ignore[no-redef]
        """Stub si OR-Tools absent (ne sera jamais passé à un vrai solver)."""
