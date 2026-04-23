#!/usr/bin/env bash
# on-create.sh — runs once when the dev container image is first created.
# Installs system-level tooling that should be baked into the image layer.
set -euo pipefail

echo "==> [on-create] Installing system packages..."
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
    curl \
    git \
    gnupg \
    jq \
    lsb-release \
    make \
    unzip \
    wget

# ── uv (fast Python package/project manager) ────────────────────────────────
if ! command -v uv &>/dev/null; then
    echo "==> [on-create] Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo 'export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
fi

# ── Azure Developer CLI (azd) ────────────────────────────────────────────────
if ! command -v azd &>/dev/null; then
    echo "==> [on-create] Installing Azure Developer CLI (azd)..."
    curl -fsSL https://aka.ms/install-azd.sh | bash
fi

# ── Yarn (global via corepack) ────────────────────────────────────────────────
if ! command -v yarn &>/dev/null; then
    echo "==> [on-create] Enabling Yarn via corepack..."
    corepack enable
    corepack prepare yarn@1.22.21 --activate
fi

echo "==> [on-create] Done."
