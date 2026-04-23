#!/usr/bin/env bash
# on-create.sh — runs once when the dev container image is first created.
# The base image (devcontainers/base:ubuntu-24.04) + features already supply
# az, curl, git, jq, etc.  We only need to add tools not covered by features.
set -euo pipefail

# ── uv (fast Python package/project manager) ────────────────────────────────
if ! command -v uv &>/dev/null; then
    echo "==> [on-create] Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
fi

# ── Azure Developer CLI (azd) ────────────────────────────────────────────────
if ! command -v azd &>/dev/null; then
    echo "==> [on-create] Installing Azure Developer CLI (azd)..."
    curl -fsSL https://aka.ms/install-azd.sh | bash
fi

# ── Yarn (global via corepack, Node feature is already installed) ─────────────
if ! command -v yarn &>/dev/null; then
    echo "==> [on-create] Enabling Yarn via corepack..."
    corepack enable
    corepack prepare yarn@1.22.21 --activate
fi

echo "==> [on-create] Done."
