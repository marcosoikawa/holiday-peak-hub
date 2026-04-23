#!/usr/bin/env bash
# post-create.sh — runs after the container is created and the repository is
# mounted. Installs all project-level Python and Node dependencies so the
# workspace is fully ready to use.
set -euo pipefail

# Make sure PATH includes uv / cargo bins installed during on-create
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║      Holiday Peak Hub — dev-container post-create       ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── 1. Install the shared library (holiday-peak-lib) ─────────────────────────
echo "==> [1/4] Installing holiday-peak-lib..."
cd "$REPO_ROOT/lib/src"
uv pip install --system -e ".[dev,test,lint]"
cd "$REPO_ROOT"

# ── 2. Install all app Python dependencies ───────────────────────────────────
echo "==> [2/4] Installing Python dependencies for all apps..."

APP_DIRS=(
    apps/crud-service/src
    apps/crm-campaign-intelligence/src
    apps/crm-profile-aggregation/src
    apps/crm-segmentation-personalization/src
    apps/crm-support-assistance/src
    apps/ecommerce-cart-intelligence/src
    apps/ecommerce-catalog-search/src
    apps/ecommerce-checkout-support/src
    apps/ecommerce-order-status/src
    apps/ecommerce-product-detail-enrichment/src
    apps/inventory-alerts-triggers/src
    apps/inventory-health-check/src
    apps/inventory-jit-replenishment/src
    apps/inventory-reservation-validation/src
    apps/logistics-carrier-selection/src
    apps/logistics-eta-computation/src
    apps/logistics-returns-support/src
    apps/logistics-route-issue-detection/src
    apps/product-management-acp-transformation/src
    apps/product-management-assortment-optimization/src
    apps/product-management-consistency-validation/src
    apps/product-management-normalization-classification/src
    apps/search-enrichment-agent/src
    apps/truth-enrichment/src
    apps/truth-export/src
    apps/truth-hitl/src
    apps/truth-ingestion/src
)

for dir in "${APP_DIRS[@]}"; do
    if [ -f "$REPO_ROOT/$dir/pyproject.toml" ]; then
        echo "   installing: $dir"
        install_err=$( uv pip install --system -e "$REPO_ROOT/$dir" 2>&1 ) || {
            echo "   [WARN] could not install $dir — this may require Azure credentials or network access."
            echo "          Error: $install_err"
        }
    fi
done

# ── 3. Install root-level dev/test tooling ───────────────────────────────────
echo "==> [3/4] Installing root dev/test tooling..."
uv pip install --system \
    pytest \
    pytest-asyncio \
    pytest-cov \
    pytest-mock \
    httpx \
    black \
    isort \
    pylint \
    ruff \
    mypy \
    pre-commit \
    debugpy \
    faker \
    python-dotenv

# ── 4. Install UI (Next.js) dependencies ─────────────────────────────────────
echo "==> [4/4] Installing UI dependencies (yarn install)..."
cd "$REPO_ROOT/apps/ui"
if ! yarn install --frozen-lockfile; then
    echo "   [WARN] yarn --frozen-lockfile failed (lockfile may be out of sync with package.json)."
    echo "          Falling back to regular yarn install — dependency versions may differ from CI."
    yarn install
fi
cd "$REPO_ROOT"

# ── 5. Set up git hooks ───────────────────────────────────────────────────────
if [ -d "$REPO_ROOT/.githooks" ]; then
    echo "==> [extra] Configuring git hooks..."
    git -C "$REPO_ROOT" config core.hooksPath .githooks
fi

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
