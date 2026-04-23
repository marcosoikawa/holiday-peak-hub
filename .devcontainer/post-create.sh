#!/usr/bin/env bash
# post-create.sh — runs after the container is created and the repository is
# mounted. Installs all project-level Python and Node dependencies so the
# workspace is fully ready to use.
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║      Holiday Peak Hub — dev-container post-create       ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── 1. Install the shared library (holiday-peak-lib) ─────────────────────────
echo "==> [1/3] Installing holiday-peak-lib (dev, test, lint extras)..."
uv pip install --system -e "lib/src[dev,test,lint]"

# ── 2. Install all app Python packages (discovered dynamically) ──────────────
echo "==> [2/3] Installing Python dependencies for all apps..."
while IFS= read -r pyproject; do
    dir="$(dirname "$pyproject")"
    echo "   installing: $dir"
    uv pip install --system -e "$dir" 2>/dev/null || \
        echo "   [WARN] skipped $dir (may need Azure credentials or network access)"
done < <(find "$REPO_ROOT/apps" -maxdepth 3 -name "pyproject.toml" | sort)

# ── 3. Install UI (Next.js) dependencies ─────────────────────────────────────
echo "==> [3/3] Installing UI dependencies (yarn install)..."
cd "$REPO_ROOT/apps/ui"
yarn install --frozen-lockfile || yarn install
cd "$REPO_ROOT"

# ── git hooks (optional) ─────────────────────────────────────────────────────
[ -d ".githooks" ] && git config core.hooksPath .githooks

echo ""
echo "✅  Dev container is ready!"
echo ""
echo "   Start the CRUD service :"
echo "     uvicorn crud_service.main:app --reload --port 8000 --app-dir apps/crud-service/src"
echo ""
echo "   Start the UI :"
echo "     cd apps/ui && yarn dev"
echo ""
echo "   Run tests :"
echo "     cd lib/src && python -m pytest ../tests"
echo ""
