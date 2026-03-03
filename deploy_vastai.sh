#!/usr/bin/env bash
# deploy_vastai.sh — Idempotent VideoAgent setup for Vast.ai Ubuntu 24.04
#
# Usage (run from the repo root after cloning):
#   bash deploy_vastai.sh
#
# What it does: 12 steps, PASS/FAIL per step, full log at /workspace/deploy.log
# Re-run safe: each step checks for existing artefacts before repeating work.
# Exits 1 if any step fails.

set -uo pipefail

REPO_DIR="/workspace/videoagent"
LOG_FILE="/workspace/deploy.log"

# Redirect all output (stdout + stderr) to tee so it goes to console + log file
exec > >(tee -a "$LOG_FILE") 2>&1

echo ""
echo "================================================================"
echo "  VideoAgent deploy — $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Repo:  $REPO_DIR"
echo "  Log:   $LOG_FILE"
echo "================================================================"
echo ""

# ── Step tracking ─────────────────────────────────────────────────────────────

declare -a _STEP_LABELS=()
declare -a _STEP_RESULTS=()
_ANY_FAIL=0

_record() {
    # _record <label> <"PASS"|"FAIL">
    _STEP_LABELS+=("$1")
    _STEP_RESULTS+=("$2")
    if [[ "$2" == "FAIL" ]]; then
        _ANY_FAIL=1
    fi
}

# ── Helpers ───────────────────────────────────────────────────────────────────

_hr() { echo "────────────────────────────────────────────────────────────────"; }
_step() { _hr; echo "STEP $1: $2"; _hr; }

_min_size() {
    # _min_size <file> <min_bytes>
    local size
    size=$(stat -c%s "$1" 2>/dev/null || echo 0)
    (( size >= $2 ))
}

# ── Step 1: System deps ───────────────────────────────────────────────────────

_step 1 "SYSTEM DEPS"
if apt-get update -qq \
   && apt-get install -y --no-install-recommends \
      git curl wget ffmpeg \
      libjpeg-dev libpng-dev build-essential python3-dev \
      nodejs npm; then
    _record "1_system_deps" "PASS"
    echo "PASS: system deps installed"
else
    _record "1_system_deps" "FAIL"
    echo "FAIL: apt-get failed"
fi

# ── Step 2: Rust (for tokenizers wheel) ──────────────────────────────────────

_step 2 "RUST"
if command -v rustup &>/dev/null; then
    echo "rustup already installed — updating stable"
    source "$HOME/.cargo/env"
    if rustup update stable; then
        _record "2_rust" "PASS"
        echo "PASS: rustup updated"
    else
        _record "2_rust" "FAIL"
        echo "FAIL: rustup update failed"
    fi
else
    echo "Installing Rust via rustup.rs..."
    if curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y; then
        source "$HOME/.cargo/env"
        rustup update stable
        _record "2_rust" "PASS"
        echo "PASS: Rust installed"
    else
        _record "2_rust" "FAIL"
        echo "FAIL: Rust install failed"
    fi
fi

# ── Step 3: Python venv ───────────────────────────────────────────────────────

_step 3 "PYTHON VENV"
cd "$REPO_DIR"
if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

if pip install --upgrade pip \
   && pip install -r requirements.txt \
   && pip install torch torchvision torchaudio \
         --index-url https://download.pytorch.org/whl/cu128 \
         --no-cache-dir; then
    _record "3_python_venv" "PASS"
    echo "PASS: python venv ready (torch cu128 — Blackwell/RTX5090 compatible)"
else
    _record "3_python_venv" "FAIL"
    echo "FAIL: pip install failed"
fi

# ── Step 4: React build ───────────────────────────────────────────────────────

_step 4 "REACT BUILD"
cd "$REPO_DIR/Web/video_agent"
if npm install && npm run build; then
    if [[ -f "dist/index.html" ]]; then
        _record "4_react_build" "PASS"
        echo "PASS: dist/index.html present"
    else
        _record "4_react_build" "FAIL"
        echo "FAIL: dist/index.html not found after build"
    fi
else
    _record "4_react_build" "FAIL"
    echo "FAIL: npm build failed"
fi
cd "$REPO_DIR"

# ── Step 5: SAM ViT-B weights (original SAM) ─────────────────────────────────

_step 5 "SAM WEIGHTS (ViT-B)"
SAM_PTH="$REPO_DIR/style/sam_vit_b.pth"
SAM_MIN_BYTES=$((300 * 1024 * 1024))   # 300 MB

if [[ -f "$SAM_PTH" ]] && _min_size "$SAM_PTH" $SAM_MIN_BYTES; then
    _record "5_sam_weights" "PASS"
    echo "PASS: $SAM_PTH already present ($(stat -c%s "$SAM_PTH") bytes)"
else
    mkdir -p "$REPO_DIR/style"
    echo "Downloading SAM ViT-B checkpoint (~375 MB)..."
    if wget -q \
         "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth" \
         -O "$SAM_PTH" \
       && _min_size "$SAM_PTH" $SAM_MIN_BYTES; then
        _record "5_sam_weights" "PASS"
        echo "PASS: downloaded $(stat -c%s "$SAM_PTH") bytes"
    else
        _record "5_sam_weights" "FAIL"
        echo "FAIL: download failed or file too small"
    fi
fi

# ── Step 6: SAM2 ─────────────────────────────────────────────────────────────

_step 6 "SAM2"
SAM2_DIR="/workspace/sam2_checkpoints"
SAM2_PT="$SAM2_DIR/sam2_hiera_large.pt"
SAM2_MIN_BYTES=$((800 * 1024 * 1024))   # 800 MB

source .venv/bin/activate

SAM2_PIP_OK=0
SAM2_DL_OK=0

if pip show sam-2 &>/dev/null 2>&1 || pip show SAM-2 &>/dev/null 2>&1; then
    echo "sam2 already installed"
    SAM2_PIP_OK=1
else
    # Install from GitHub with --no-deps to avoid triggering a torch build
    # (PyPI sam2 requires torch>=2.5.1 as a build dep which conflicts with
    #  DiffuEraser's pinned torch==2.3.1 and causes disk-exhausting downloads)
    if pip install "git+https://github.com/facebookresearch/sam2.git" --no-deps \
       && pip install hydra-core iopath; then
        SAM2_PIP_OK=1
    else
        echo "FAIL: sam2 install failed"
    fi
fi

if [[ -f "$SAM2_PT" ]] && _min_size "$SAM2_PT" $SAM2_MIN_BYTES; then
    echo "sam2_hiera_large.pt already present ($(stat -c%s "$SAM2_PT") bytes)"
    SAM2_DL_OK=1
else
    mkdir -p "$SAM2_DIR"
    echo "Downloading sam2_hiera_large.pt from HuggingFace (~900 MB)..."
    if python3 - <<'PYEOF'
from huggingface_hub import hf_hub_download
hf_hub_download(
    "facebook/sam2-hiera-large",
    "sam2_hiera_large.pt",
    local_dir="/workspace/sam2_checkpoints",
)
print("download ok")
PYEOF
    then
        SAM2_DL_OK=1
    else
        echo "FAIL: sam2_hiera_large.pt download failed"
    fi
fi

if (( SAM2_PIP_OK && SAM2_DL_OK )); then
    _record "6_sam2" "PASS"
    echo "PASS: SAM2 ready"
else
    _record "6_sam2" "FAIL"
fi

# ── Step 7: DiffuEraser ───────────────────────────────────────────────────────

_step 7 "DIFFUERASER"
DE_DIR="/workspace/DiffuEraser"
DE_WEIGHTS="$DE_DIR/weights"

DE_CLONE_OK=0
DE_REQS_OK=0
DE_WEIGHTS_OK=0

if [[ -d "$DE_DIR/.git" ]]; then
    echo "DiffuEraser repo already cloned"
    DE_CLONE_OK=1
else
    if git clone https://github.com/lixiaowen-xw/DiffuEraser.git "$DE_DIR"; then
        DE_CLONE_OK=1
    else
        echo "FAIL: git clone DiffuEraser failed"
    fi
fi

if (( DE_CLONE_OK )); then
    source .venv/bin/activate
    # Skip torch/torchvision/torchaudio — DiffuEraser pins torch==2.3.1 which
    # lacks Blackwell (sm_120) support and bloats disk. Torch is already
    # installed at cu128 from Step 3.
    grep -v -E "^torch==|^torchvision==|^torchaudio==" "$DE_DIR/requirements.txt" \
        > /tmp/de_reqs_notorch.txt
    if pip install -r /tmp/de_reqs_notorch.txt; then
        DE_REQS_OK=1
    else
        echo "FAIL: pip install DiffuEraser requirements failed"
    fi
fi

if [[ -n "$(ls -A "$DE_WEIGHTS" 2>/dev/null)" ]]; then
    echo "DiffuEraser weights already present"
    DE_WEIGHTS_OK=1
else
    mkdir -p "$DE_WEIGHTS"
    echo "Downloading DiffuEraser weights from HuggingFace (~7 GB)..."
    if python3 - <<'PYEOF'
from huggingface_hub import snapshot_download
snapshot_download(
    "lixiaowen-xw/DiffuEraser",
    local_dir="/workspace/DiffuEraser/weights",
)
print("download ok")
PYEOF
    then
        DE_WEIGHTS_OK=1
    else
        echo "FAIL: DiffuEraser weights download failed"
    fi
fi

if (( DE_CLONE_OK && DE_REQS_OK && DE_WEIGHTS_OK )); then
    _record "7_diffueraser" "PASS"
    echo "PASS: DiffuEraser ready"
else
    _record "7_diffueraser" "FAIL"
fi

# ── Step 8: Stable Diffusion v1.5 ────────────────────────────────────────────

_step 8 "STABLE DIFFUSION 1.5"
SD_DIR="/workspace/stable-diffusion-v1-5"

if [[ -n "$(ls -A "$SD_DIR" 2>/dev/null)" ]]; then
    echo "Stable Diffusion v1.5 already present"
    _record "8_stable_diffusion" "PASS"
    echo "PASS: $SD_DIR already populated"
else
    mkdir -p "$SD_DIR"
    echo "Downloading Stable Diffusion v1.5 from HuggingFace (~4 GB, diffusers format only)..."
    # Excludes: monolithic .ckpt checkpoints, pruned variants, safety_checker (~25 GB saved)
    # HuggingFace snapshot_download also caches to ~/.cache — purge after to free disk.
    if python3 - <<'PYEOF'
from huggingface_hub import snapshot_download
snapshot_download(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    local_dir="/workspace/stable-diffusion-v1-5",
    ignore_patterns=["*.ot", "*.msgpack", "*.ckpt", "v1-5-pruned*", "safety_checker*"],
)
print("download ok")
PYEOF
    then
        echo "Cleaning HuggingFace download cache (~20 GB freed)..."
        rm -rf ~/.cache/huggingface/hub/
        _record "8_stable_diffusion" "PASS"
        echo "PASS: Stable Diffusion v1.5 downloaded"
    else
        _record "8_stable_diffusion" "FAIL"
        echo "FAIL: Stable Diffusion v1.5 download failed"
    fi
fi

# ── Step 9: Env file ──────────────────────────────────────────────────────────

_step 9 "ENV FILE"
cd "$REPO_DIR"
if [[ -f ".env" ]]; then
    echo ".env already exists — skipping"
    _record "9_env_file" "PASS"
    echo "PASS: .env present (not overwritten)"
else
    if cp .env.example .env; then
        _record "9_env_file" "PASS"
        echo "PASS: .env created from .env.example"
        echo "WARNING: Review and edit .env before starting VideoAgent"
    else
        _record "9_env_file" "FAIL"
        echo "FAIL: could not copy .env.example to .env"
    fi
fi

# ── Step 10: Directories ──────────────────────────────────────────────────────

_step 10 "DIRECTORIES"
cd "$REPO_DIR"
if mkdir -p \
    raw_clips \
    output/inpainted \
    output/inpaint_jobs \
    output/inpaint_temp \
    logs; then
    _record "10_directories" "PASS"
    echo "PASS: runtime directories created"
else
    _record "10_directories" "FAIL"
    echo "FAIL: mkdir failed"
fi

# ── Step 11: GPU smoke test ───────────────────────────────────────────────────

_step 11 "GPU SMOKE TEST"
source .venv/bin/activate
if python3 - <<'PYEOF'
import torch
assert torch.cuda.is_available(), "CUDA not available"
print("GPU:", torch.cuda.get_device_name(0))
PYEOF
then
    _record "11_gpu_smoke_test" "PASS"
    echo "PASS: CUDA GPU confirmed"
else
    _record "11_gpu_smoke_test" "FAIL"
    echo "FAIL: torch.cuda.is_available() returned False"
fi

# ── Step 12: Summary table ────────────────────────────────────────────────────

echo ""
echo "================================================================"
echo "  DEPLOYMENT SUMMARY"
echo "================================================================"
printf "  %-30s  %s\n" "STEP" "RESULT"
printf "  %-30s  %s\n" "──────────────────────────────" "──────"
for i in "${!_STEP_LABELS[@]}"; do
    label="${_STEP_LABELS[$i]}"
    result="${_STEP_RESULTS[$i]}"
    if [[ "$result" == "PASS" ]]; then
        printf "  %-30s  \033[0;32m%s\033[0m\n" "$label" "$result"
    else
        printf "  %-30s  \033[0;31m%s\033[0m\n" "$label" "$result"
    fi
done
echo "================================================================"
echo "  Full log: $LOG_FILE"
echo "================================================================"
echo ""

if (( _ANY_FAIL )); then
    echo "RESULT: FAILED — one or more steps did not pass"
    exit 1
else
    echo "RESULT: ALL STEPS PASSED"
    echo ""
    echo "Start VideoAgent:"
    echo "  cd $REPO_DIR && source .venv/bin/activate && python -m app"
    exit 0
fi
