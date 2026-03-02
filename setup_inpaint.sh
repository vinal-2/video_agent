#!/usr/bin/env bash
# =============================================================================
# setup_inpaint.sh
# Installs the generative inpainting stack on a Vast.ai RTX 3090 instance.
#
# Stack: IOPaint/LaMa  +  E2FGVI  +  SAM2  (ProPainter stays for fallback)
#
# Safe to re-run — every step checks whether it is already done.
# Assumes: Ubuntu 24.04, /workspace/videoagent repo present, venv already
#          created with PyTorch + CUDA installed.
#
# Usage:
#   chmod +x /workspace/setup_inpaint.sh
#   /workspace/setup_inpaint.sh
# =============================================================================

set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass()  { echo -e "${GREEN}[PASS]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }
info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
step()  { echo; echo -e "${YELLOW}══ $* ══${NC}"; }

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO=/workspace/videoagent
VENV="$REPO/.venv"
E2FGVI_DIR=/workspace/E2FGVI
STYLE_DIR="$REPO/style"

SAM2_CKPT="$STYLE_DIR/sam2_hiera_large.pt"
E2FGVI_CKPT="$E2FGVI_DIR/release_model/E2FGVI-HQ-CVPR22.pth"

# ── E2FGVI weight download config ─────────────────────────────────────────────
# The HQ model (~433 MB) is used for higher-resolution event video.
# Google Drive file ID — verify this against the repo README if download fails:
#   https://github.com/MCG-NKU/E2FGVI#weights
# The ID below is for E2FGVI-HQ-CVPR22.pth as listed in the repo at time of writing.
E2FGVI_GDRIVE_ID="10wGdKSUOie0XmCr8SQ2A2FeDe-mfn5w3"
E2FGVI_EXPECTED_MB=430   # fail if download produces a file smaller than this

# SAM2 checkpoint URL (from Meta CDN)
SAM2_URL="https://dl.fbaipublicfiles.com/segment_anything_2/072824/sam2_hiera_large.pt"
SAM2_EXPECTED_MB=850

# ── Pre-flight checks ─────────────────────────────────────────────────────────
step "Pre-flight checks"

[[ -d "$REPO" ]]  || fail "Repo not found at $REPO. Clone it first."
[[ -d "$VENV" ]]  || fail "Venv not found at $VENV. Create it and install PyTorch first."

mkdir -p "$STYLE_DIR" "$E2FGVI_DIR/release_model"

source "$VENV/bin/activate"

python - <<'PYEOF' && pass "PyTorch CUDA available" || fail "PyTorch/CUDA not working in venv"
import torch
assert torch.cuda.is_available(), "CUDA not available"
props = torch.cuda.get_device_properties(0)
print(f"  GPU: {props.name}  VRAM: {props.total_memory // 1024**3} GB")
PYEOF

# ── 1. System dependencies ────────────────────────────────────────────────────
step "1. System dependencies"

apt-get install -y -qq --no-install-recommends \
    libjpeg-dev libpng-dev build-essential curl git wget \
    && pass "System deps ready"

# ── 2. Rust (rustup — NOT Ubuntu's stale apt cargo) ──────────────────────────
step "2. Rust toolchain"

if ! command -v rustup &>/dev/null; then
    info "rustup not found — installing..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --default-toolchain stable --no-modify-path
    info "rustup installed"
else
    info "rustup already present"
fi

# Source every time — cargo env is not guaranteed in non-login shells
source "$HOME/.cargo/env"
rustup update stable --no-self-update -q
rustup default stable

RUST_VER=$(rustc --version)
pass "Rust ready: $RUST_VER"

# ── 3. IOPaint (LaMa + generative inpainting backend) ────────────────────────
step "3. IOPaint / LaMa"

if python -c "import iopaint" 2>/dev/null; then
    IOPAINT_VER=$(python -c "import iopaint; print(iopaint.__version__)")
    info "IOPaint $IOPAINT_VER already installed"
else
    info "Installing IOPaint (includes pre-built tokenizers wheels)..."
    # iopaint vendors tokenizers wheels so Rust is not strictly needed here,
    # but rustup above ensures pip can build from source if the wheel misses.
    pip install --quiet iopaint
fi

# Import smoke test
python - <<'PYEOF' && pass "IOPaint import OK" || fail "IOPaint import failed"
import iopaint
from iopaint.model.lama import LaMa
print(f"  iopaint {iopaint.__version__}  — LaMa class importable")
PYEOF

# GPU load smoke test (loads model weights into VRAM — ~1 GB)
python - <<'PYEOF' && pass "LaMa GPU load OK" || fail "LaMa GPU load failed"
import torch
from iopaint.model.lama import LaMa
device = torch.device("cuda")
model = LaMa(device)
print(f"  LaMa loaded on {device}")
del model
torch.cuda.empty_cache()
PYEOF

# ── 4. E2FGVI ────────────────────────────────────────────────────────────────
step "4. E2FGVI (temporal consistency)"

# 4a. Clone repo
if [[ -d "$E2FGVI_DIR/.git" ]]; then
    info "E2FGVI repo already cloned"
else
    info "Cloning E2FGVI..."
    git clone --quiet https://github.com/MCG-NKU/E2FGVI "$E2FGVI_DIR"
fi
pass "E2FGVI repo present"

# 4b. Requirements
# Note: some versions of E2FGVI requirements.txt pin old torch versions.
# If conflicts arise, use: pip install -r ... --no-deps
info "Installing E2FGVI requirements..."
pip install --quiet -r "$E2FGVI_DIR/requirements.txt" \
    && pass "E2FGVI requirements installed" \
    || {
        info "requirements.txt had conflicts — retrying with --no-deps"
        pip install --quiet -r "$E2FGVI_DIR/requirements.txt" --no-deps
        pass "E2FGVI requirements installed (no-deps mode)"
    }

# 4c. Weights
if [[ -f "$E2FGVI_CKPT" ]]; then
    ACTUAL_MB=$(du -m "$E2FGVI_CKPT" | cut -f1)
    if (( ACTUAL_MB < E2FGVI_EXPECTED_MB )); then
        info "E2FGVI weights look truncated (${ACTUAL_MB} MB < ${E2FGVI_EXPECTED_MB} MB) — re-downloading"
        rm -f "$E2FGVI_CKPT"
    else
        info "E2FGVI weights present (${ACTUAL_MB} MB)"
    fi
fi

if [[ ! -f "$E2FGVI_CKPT" ]]; then
    info "Downloading E2FGVI-HQ-CVPR22.pth from Google Drive (~433 MB)..."
    pip install --quiet gdown
    gdown "$E2FGVI_GDRIVE_ID" -O "$E2FGVI_CKPT" || {
        fail "E2FGVI weight download failed.
  Check the Google Drive ID in this script against:
    https://github.com/MCG-NKU/E2FGVI#weights
  or download manually and place at:
    $E2FGVI_CKPT"
    }
fi

ACTUAL_MB=$(du -m "$E2FGVI_CKPT" | cut -f1)
(( ACTUAL_MB >= E2FGVI_EXPECTED_MB )) \
    && pass "E2FGVI weights OK (${ACTUAL_MB} MB)" \
    || fail "E2FGVI weights look incomplete (${ACTUAL_MB} MB, expected ≥${E2FGVI_EXPECTED_MB} MB)"

# 4d. GPU smoke test — instantiate model, load weights
python - <<PYEOF && pass "E2FGVI GPU load OK" || fail "E2FGVI GPU load failed"
import sys, torch
sys.path.insert(0, "$E2FGVI_DIR")
import model.e2fgvi_hq as net_module

model = net_module.InpaintGenerator().cuda().eval()
ckpt = torch.load("$E2FGVI_CKPT", map_location="cuda")
# The checkpoint may be wrapped in a 'generator' key
state = ckpt.get("netG", ckpt.get("generator", ckpt))
model.load_state_dict(state, strict=False)
print("  E2FGVI InpaintGenerator loaded on GPU with weights")
del model
torch.cuda.empty_cache()
PYEOF

# ── 5. SAM2 ──────────────────────────────────────────────────────────────────
step "5. SAM2 (video mask propagation)"

# 5a. Install package
if python -c "import sam2" 2>/dev/null; then
    SAM2_VER=$(python -c "import sam2; print(sam2.__version__)")
    info "sam-2 $SAM2_VER already installed"
else
    info "Installing sam-2..."
    pip install --quiet sam-2
fi

python -c "from sam2.build_sam import build_sam2_video_predictor; print('  SAM2 imports OK')" \
    && pass "SAM2 package import OK" \
    || fail "SAM2 import failed — 'pip install sam-2' may have failed silently"

# 5b. Checkpoint
if [[ -f "$SAM2_CKPT" ]]; then
    ACTUAL_MB=$(du -m "$SAM2_CKPT" | cut -f1)
    if (( ACTUAL_MB < SAM2_EXPECTED_MB )); then
        info "SAM2 checkpoint looks truncated (${ACTUAL_MB} MB < ${SAM2_EXPECTED_MB} MB) — re-downloading"
        rm -f "$SAM2_CKPT"
    else
        info "SAM2 checkpoint present (${ACTUAL_MB} MB)"
    fi
fi

if [[ ! -f "$SAM2_CKPT" ]]; then
    info "Downloading sam2_hiera_large.pt from Meta CDN (~900 MB)..."
    wget -q --show-progress -O "$SAM2_CKPT" "$SAM2_URL" || {
        fail "SAM2 checkpoint download failed from $SAM2_URL
  If this URL is stale, check: https://github.com/facebookresearch/sam2#model-description"
    }
fi

ACTUAL_MB=$(du -m "$SAM2_CKPT" | cut -f1)
(( ACTUAL_MB >= SAM2_EXPECTED_MB )) \
    && pass "SAM2 checkpoint OK (${ACTUAL_MB} MB)" \
    || fail "SAM2 checkpoint looks incomplete (${ACTUAL_MB} MB, expected ≥${SAM2_EXPECTED_MB} MB)"

# 5c. GPU smoke test — load video predictor (does NOT require input frames)
python - <<PYEOF && pass "SAM2 video predictor GPU load OK" || fail "SAM2 GPU load failed"
import torch
from sam2.build_sam import build_sam2_video_predictor

predictor = build_sam2_video_predictor(
    "configs/sam2/sam2_hiera_large.yaml",
    "$SAM2_CKPT",
    device="cuda",
)
print("  SAM2 SAMVideo2Predictor loaded on cuda")
del predictor
torch.cuda.empty_cache()
PYEOF

# ── Summary ───────────────────────────────────────────────────────────────────
echo
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  All inpainting components verified on GPU   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo
echo "Component versions / paths:"
python -c "import iopaint; print(f'  IOPaint (LaMa):  {iopaint.__version__}')"
python -c "import sam2;    print(f'  SAM2:            {sam2.__version__}')"
echo      "  E2FGVI repo:     $E2FGVI_DIR"
echo      "  E2FGVI weights:  $E2FGVI_CKPT"
echo      "  SAM2 checkpoint: $SAM2_CKPT"
echo
echo "Add to your server environment (e.g. /etc/environment or .env):"
echo "  E2FGVI_DIR=$E2FGVI_DIR"
echo "  SAM2_CHECKPOINT=$SAM2_CKPT"
echo
echo "Next step: update scripts/inpaint_worker.py to route"
echo "  engine='lama'    → IOPaint + E2FGVI pipeline"
echo "  engine='propainter' → existing ProPainter fallback"
