#!/usr/bin/env bash
# =============================================================================
# create-password.sh — émet un code d'accès TimeTabler et l'installe sur le M4.
# Usage :
#   ./create-password.sh AUBLANC             # code = "AUBLANC" (mémorable, jamais stocké)
#   ./create-password.sh "Dupont" MONCODE    # code = "MONCODE", libellé "Dupont"
#
# Fait : sha256(code) → append tokens_sha256.txt (M4 + trace locale).
# Le code en clair n'est sauvé nulle part ; l'utilisateur le tape directement.
# ✅ PAS de restart : le M4 relit tokens_sha256.txt à la volée (live-reload).
# ⚠️ Script perso, tu le lances toi-même (il fait du ssh vers le M4).
# ⚠️ Prérequis : le M4 doit tourner la version live-reload de server.py.
# =============================================================================
set -euo pipefail

# --- Config M4 (⚠️ confirmer via : ssh M4 "cat ~/Library/LaunchAgents/*imetabler*") ---
M4="dnavatar-ia@100.92.160.97"
M4_TOKENS='~/services/timetabler/solver/tokens_sha256.txt'
LOCAL_TOKENS="/Users/dnavatar/Desktop/_dnavatar/apps/timetabler-solver/tokens_sha256.txt"

LABEL="${1:-code}"
CODE="${2:-$1}"   # 1 arg → le libellé EST le code (ex. AUBLANC) ; 2 args → code explicite

HASH=$(printf '%s' "$CODE" | shasum -a 256 | cut -d' ' -f1)   # minuscules, comme le front
LINE="$HASH  # $LABEL $(date +%Y-%m-%d)"

echo "→ Code   : $CODE"
echo "→ sha256 : $HASH"

printf '%s\n' "$LINE" >> "$LOCAL_TOKENS"
echo "✓ trace locale ajoutée"

ssh "$M4" "printf '%s\n' \"$LINE\" >> $M4_TOKENS"
echo "✓ ajouté sur le M4 (actif immédiatement — live-reload, pas de restart)"

echo ""
echo "═══════════════════════════════════════"
echo "  CODE À ENVOYER : $CODE"
echo "  (lié à la 1re machine qui l'utilisera)"
echo "═══════════════════════════════════════"
