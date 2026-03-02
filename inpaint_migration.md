# VideoAgent — Inpainting Pipeline Migration
## Claude Code Implementation Guide

**Date:** March 2026  
**Hardware:** Vast.ai RTX 3090 24GB VRAM, Ubuntu 24.04, /workspace/videoagent  
**Replacing:** ProPainter (pixel propagation)  
**With:** SAM2 → DiffuEraser → (optional TC-Net)  
**Goal:** Crowd/person removal from event videos with full background hallucination  
**Dev workflow:** Build locally at D:\video-agent → push to GitHub → pull and run on Vast.ai

---

## Additional Repo Evaluation

Three repos were considered before finalising the pipeline.

### video-object-remover (oguzakif)
**Verdict: SKIP**  
Uses SiamMask + FGT — both 2021-era propagation models with the same fundamental limitation as ProPainter. FGT fills from visible background pixels in neighbouring frames. When background is never visible (crowd blocking stage), it fails identically to ProPainter. Last meaningful commit 2022. Not worth integrating.

### Inpaint-Anything (geekyutao)
**Verdict: REFERENCE ARCHITECTURE — study, do not integrate as-is**  
Closest existing project to what we're building. Chains SAM (click to segment) → LaMa or SD inpainting. Image-only (no video), uses SAM v1, uses LaMa — but the SAM → inpaint glue code is directly applicable. Specifically `remove_anything.py` shows how to convert a point click into a SAM mask and pass it to an inpainter. Read this before writing `diffueraser_worker.py` Step 2.  
Reference: `https://github.com/geekyutao/Inpaint-Anything/blob/main/remove_anything.py`

### Infusion (ncherel)
**Verdict: FUTURE OPTION — monitor, do not integrate now**  
Genuinely novel — fine-tunes a small internal diffusion model on the specific video clip itself (~0.5M params, zero-shot per-video). Temporal consistency without a separate pass. The limitation is setup time: needs several minutes of per-clip optimisation before inference starts. Not practical for a tool expecting fast turnaround. If DiffuEraser produces flickering that TC-Net doesn't fix, revisit Infusion as a quality ceiling test. Add to the watch list alongside ROSE.

---

## Cross-Platform Portability — Build Local, Deploy on Vast.ai

The goal: code is written and tested on Windows (D:\video-agent), pushed to GitHub, then a fresh Vast.ai instance clones the repo and runs a single deploy script. No manual file copying, no environment drift between machines.

### What lives in GitHub (tracked)
- All Python scripts (`scripts/`, `app.py`)
- React frontend source (`Web/video_agent/src/`)
- `.env.example` (template with no secrets)
- `deploy_vastai.sh` (one-command server setup)
- `requirements.txt`
- `CLAUDE.md`, `SESSIONS.md`, docs

### What does NOT live in GitHub (gitignored)
- `.env` — secrets and machine-specific paths
- `raw_clips/` — source footage, too large
- `output/` — generated files
- `style/*.pth` — model weights, too large
- `Web/video_agent/dist/` — build artifact, regenerated on server
- `.venv/` — Python venv, rebuilt on server
- External repos: DiffuEraser, SAM2 checkpoints (installed by deploy script)

### .gitignore additions needed
```
.env
raw_clips/
output/
style/*.pth
Web/video_agent/dist/
Web/video_agent/node_modules/
__pycache__/
*.pyc
.venv/
logs/
*.log
```

### Path Strategy — No Hardcoded Paths

All paths resolve through environment variables with Linux defaults. Same code runs on both platforms.

**Windows D:\video-agent\\.env:**
```
BASE_DIR=D:/video-agent
RAW_CLIPS_DIR=D:/video-agent/raw_clips
OUTPUT_DIR=D:/video-agent/output
STYLE_DIR=D:/video-agent/style
SAM_CHECKPOINT=D:/video-agent/style/sam_vit_b.pth
SAM2_CHECKPOINT=D:/video-agent/style/sam2_hiera_large.pt
SAM2_MODEL_CFG=sam2_hiera_large
DIFFUERASER_DIR=D:/video-agent/DiffuEraser
DIFFUERASER_WEIGHTS=D:/video-agent/DiffuEraser/weights
SD15_DIR=D:/video-agent/stable-diffusion-v1-5
FLASK_HOST=127.0.0.1
FLASK_PORT=5100
FLASK_DEBUG=True
```

**Vast.ai /workspace/videoagent/.env:**
```
BASE_DIR=/workspace/videoagent
RAW_CLIPS_DIR=/workspace/videoagent/raw_clips
OUTPUT_DIR=/workspace/videoagent/output
STYLE_DIR=/workspace/videoagent/style
SAM_CHECKPOINT=/workspace/videoagent/style/sam_vit_b.pth
SAM2_CHECKPOINT=/workspace/sam2_checkpoints/sam2_hiera_large.pt
SAM2_MODEL_CFG=sam2_hiera_large
DIFFUERASER_DIR=/workspace/DiffuEraser
DIFFUERASER_WEIGHTS=/workspace/DiffuEraser/weights
SD15_DIR=/workspace/stable-diffusion-v1-5
FLASK_HOST=0.0.0.0
FLASK_PORT=5100
FLASK_DEBUG=False
```

On Vast.ai, DiffuEraser and SAM2 live outside the repo at `/workspace/` because they are large external repos installed by the deploy script, not part of your codebase.

---

## Context for Claude Code (Paste This First Every Session)

```
You are working on VideoAgent — a Flask/React video pipeline.

LOCAL: D:\video-agent (Windows development)
SERVER: /workspace/videoagent (Vast.ai RTX 3090, Ubuntu 24.04)
REPO: GitHub (source of truth — commit all working changes)

Key facts:
- Flask backend: app.py (port 5100)
- React frontend: Web/video_agent/src/
- Scripts: scripts/ folder
- ProPainter has been REMOVED entirely. inpaint_worker.py is gone.
- lama_worker.py and e2fgvi_worker.py exist as secondary engines.
- Primary engine being added: DiffuEraser via scripts/diffueraser_worker.py
- Remote server: ssh -p 27484 root@213.224.31.105
- Venv: /workspace/videoagent/.venv
- All source clips: Samsung 4K 60fps, rotation metadata = -90 degrees
  ffmpeg MUST use -noautorotate flag + transpose=2 filter on all clip operations.

PORTABILITY RULE: No hardcoded paths anywhere in Python or shell scripts.
All paths via os.environ.get("VAR", "/workspace/default"). 
.env is never committed. .env.example is the reference template.

everything-claude-code is installed at D:\video-agent for skills/agents/hooks.
Use /plan before major tasks. Use /code-review after each completed step.

Read CLAUDE.md and SESSIONS.md in full before any task.
```

---

## Architecture

```
User draws mask on ONE keyframe in React UI
              ↓
POST /api/inpaint/start
{ engine: "diffueraser", segment_index, video_path, start, end, mask_b64 }
              ↓
app.py → diffueraser_worker.py
              ↓
┌─────────────────────────────────────────────┐
│  STEP 1: Extract frames                     │
│  ffmpeg -noautorotate + transpose=2         │
│  → output/inpaint_temp/<job_id>/frames/     │
│                                             │
│  STEP 2: SAM2 mask propagation              │
│  Point prompt from mask_b64 centroid        │
│  Propagates all frames forward + backward   │
│  del predictor + torch.cuda.empty_cache()   │
│  → output/inpaint_temp/<job_id>/masks/      │
│                                             │
│  STEP 3: DiffuEraser                        │
│  Temporal attention video inpainting        │
│  960x540 (fallback 640x360 on OOM)          │
│  → output/inpaint_temp/<job_id>/result.mp4  │
│                                             │
│  STEP 4: Finalise                           │
│  Copy → output/inpainted/<job_id>.mp4       │
│  Write status: done                         │
│  Cleanup temp dir                           │
└─────────────────────────────────────────────┘
              ↓
React polls GET /api/inpaint/status/<job_id>
              ↓
Result shown in Inpaint Tab
```

---

## Migration Steps — Run In Order

---

### STEP 0 — Cross-platform portability (do this first, once)

**Prompt for Claude Code:**

```
/plan

Task: Make VideoAgent cross-platform portable.
Build: D:\video-agent (Windows) | Deploy: /workspace/videoagent (Ubuntu, Vast.ai)

Do in order:

1. Create D:\video-agent\.env.example
   Copy current .env structure, replace all values with placeholder comments.
   Document both Windows and Linux paths in comments.

2. Update D:\video-agent\.gitignore
   Ensure excluded: .env, raw_clips/, output/, style/*.pth,
   Web/video_agent/dist/, Web/video_agent/node_modules/,
   __pycache__/, *.pyc, .venv/, logs/, *.log

3. Audit all Python files in scripts/ and app.py
   Find every hardcoded path not using os.environ.get()
   Fix each to: os.environ.get("VAR_NAME", "/workspace/sensible_default")
   The Linux /workspace path is the default — Windows users set their .env.

4. Create D:\video-agent\deploy_vastai.sh (see spec in DEPLOY SCRIPT section below)

5. Update CLAUDE.md with portability rules:
   - All paths via env vars
   - .env.example is the reference
   - Never commit .env or weights
   - deploy_vastai.sh is the single setup command for new servers

After each file: python3 -m py_compile <file> (for Python files)
Run git status to show what would be committed.
```

---

### DEPLOY SCRIPT SPEC (for Step 0 prompt above)

**Prompt for Claude Code — create deploy_vastai.sh:**

```
Create deploy_vastai.sh at D:\video-agent\deploy_vastai.sh

This sets up a fresh Vast.ai Ubuntu 24.04 instance from a clean git clone.
Must be idempotent. Print PASS/FAIL for each step. Log to /workspace/deploy.log.

Steps in order:

1. SYSTEM DEPS
   apt-get update -qq && apt-get install -y git curl wget ffmpeg
   libjpeg-dev libpng-dev build-essential python3-dev nodejs npm

2. RUST (for tokenizers wheel)
   Check if rustup installed. If not: install via rustup.rs installer.
   source $HOME/.cargo/env && rustup update stable

3. PYTHON VENV
   cd /workspace/videoagent
   python3 -m venv .venv && source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt

4. REACT BUILD
   cd Web/video_agent && npm install && npm run build
   Verify dist/index.html exists

5. SAM WEIGHTS (original SAM ViT-B)
   If style/sam_vit_b.pth missing:
   wget -q https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth
         -O style/sam_vit_b.pth
   Verify size > 300MB

6. SAM2
   If /workspace/sam2_checkpoints/sam2_hiera_large.pt missing:
   pip install sam-2
   python3 -c "from huggingface_hub import hf_hub_download;
   hf_hub_download('facebook/sam2-hiera-large','sam2_hiera_large.pt',
   local_dir='/workspace/sam2_checkpoints')"
   Verify size > 800MB

7. DIFFUERASER
   If /workspace/DiffuEraser missing:
   git clone https://github.com/lixiaowen-xw/DiffuEraser.git /workspace/DiffuEraser
   pip install -r /workspace/DiffuEraser/requirements.txt
   If /workspace/DiffuEraser/weights empty:
   python3 -c "from huggingface_hub import snapshot_download;
   snapshot_download('lixiaowen-xw/DiffuEraser',
   local_dir='/workspace/DiffuEraser/weights')"

8. STABLE DIFFUSION 1.5
   If /workspace/stable-diffusion-v1-5 missing:
   python3 -c "from huggingface_hub import snapshot_download;
   snapshot_download('stable-diffusion-v1-5/stable-diffusion-v1-5',
   local_dir='/workspace/stable-diffusion-v1-5',
   ignore_patterns=['*.ot','*.msgpack'])"

9. ENV FILE
   If .env missing: cp .env.example .env && echo "WARNING: Edit .env before starting"
   If .env exists: skip

10. DIRECTORIES
    mkdir -p raw_clips output/inpainted output/inpaint_jobs output/inpaint_temp logs

11. GPU SMOKE TEST
    python3 -c "import torch; assert torch.cuda.is_available();
    print('GPU:', torch.cuda.get_device_name(0))"

12. SUMMARY TABLE — print PASS/FAIL for each step, exit 1 if any failed
```

---

### STEP 1 — Audit current state

**Prompt for Claude Code:**

```
Do not write any code.

Read and report:
1. app.py — /api/inpaint/start route: what engines, what imports?
2. scripts/ — list all files, flag any ProPainter/Colab references
3. InpaintTab.tsx — what engines shown? What sent to API?
4. .env — list variables (mask secret values with ***)
5. Run: grep -r "ProPainter\|propainter\|colab\|DRIVE_SYNC\|inpaint_worker"
   --include="*.py" --include="*.tsx" --include="*.ts" .
6. Run: grep -rn "D:\\\\video\|C:\\\\Users\|/workspace" --include="*.py" .
   These are portability violations.

Report only. No changes.
```

---

### STEP 2 — DiffuEraser server setup (manual, large downloads)

```bash
ssh -p 27484 root@213.224.31.105

# If deploy_vastai.sh is ready from Step 0:
cd /workspace/videoagent && chmod +x deploy_vastai.sh
./deploy_vastai.sh 2>&1 | tee /workspace/deploy.log

# Or manually:
source /workspace/videoagent/.venv/bin/activate
git clone https://github.com/lixiaowen-xw/DiffuEraser.git /workspace/DiffuEraser
cd /workspace/DiffuEraser
pip install -r requirements.txt --break-system-packages -q 2>&1 | tail -10

python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('lixiaowen-xw/DiffuEraser', local_dir='/workspace/DiffuEraser/weights')
snapshot_download('stable-diffusion-v1-5/stable-diffusion-v1-5',
                  local_dir='/workspace/stable-diffusion-v1-5',
                  ignore_patterns=['*.ot','*.msgpack'])
print('Done')
"
df -h /workspace  # verify disk space
```

---

### STEP 3 — SAM2 server setup

```bash
source /workspace/videoagent/.venv/bin/activate
pip install sam-2 --break-system-packages -q
mkdir -p /workspace/sam2_checkpoints
python3 -c "
from huggingface_hub import hf_hub_download
hf_hub_download('facebook/sam2-hiera-large', 'sam2_hiera_large.pt',
                local_dir='/workspace/sam2_checkpoints')
print('SAM2 done')
"
python3 -c "from sam2.build_sam import build_sam2_video_predictor; print('Import OK')"

# Update .env
cat >> /workspace/videoagent/.env << 'EOF'
SAM2_CHECKPOINT=/workspace/sam2_checkpoints/sam2_hiera_large.pt
SAM2_MODEL_CFG=sam2_hiera_large
DIFFUERASER_DIR=/workspace/DiffuEraser
DIFFUERASER_WEIGHTS=/workspace/DiffuEraser/weights
SD15_DIR=/workspace/stable-diffusion-v1-5
EOF
```

---

### STEP 4 — Create diffueraser_worker.py

First, read DiffuEraser's actual API on the server:

```bash
cat /workspace/DiffuEraser/README.md
head -100 /workspace/DiffuEraser/inference_diffueraser.py
ls /workspace/DiffuEraser/weights/
```

Paste that output to Claude Code, then:

**Prompt for Claude Code:**

```
/plan

I am pasting DiffuEraser's README and inference script so you understand the actual API.
Also read scripts/lama_worker.py to match its status format.
Also read: https://github.com/geekyutao/Inpaint-Anything/blob/main/remove_anything.py
for the SAM → inpaint mask flow pattern.

https://raw.githubusercontent.com/geekyutao/Inpaint-Anything/refs/heads/main/README.md

<p align="center">
  <img src="./example/IAM.png">
</p>

# Inpaint Anything: Segment Anything Meets Image Inpainting
Inpaint Anything can inpaint anything in **images**, **videos** and **3D scenes**!
- Authors: Tao Yu, Runseng Feng, Ruoyu Feng, Jinming Liu, Xin Jin, Wenjun Zeng and Zhibo Chen.
- Institutes: University of Science and Technology of China; Eastern Institute for Advanced Study.
- [[Paper](https://arxiv.org/abs/2304.06790)] [[Website](https://huggingface.co/spaces/InpaintAI/Inpaint-Anything)] [[Hugging Face Homepage](https://huggingface.co/InpaintAI)]
<p align="center">
  <img src="./example/MainFramework.png" width="100%">
</p>

TL; DR: Users can select any object in an image by clicking on it. With powerful vision models, e.g., [SAM](https://arxiv.org/abs/2304.02643), [LaMa](https://arxiv.org/abs/2109.07161) and [Stable Diffusion (SD)](https://arxiv.org/abs/2112.10752), **Inpaint Anything** is able to remove the object smoothly (i.e., *Remove Anything*). Further, prompted by user input text, Inpaint Anything can fill the object with any desired content (i.e., *Fill Anything*) or replace the background of it arbitrarily (i.e., *Replace Anything*).

## 📜 News
[2023/9/15] [Remove Anything 3D](#remove-anything-3d) code is available!\
[2023/4/30] [Remove Anything Video](#remove-anything-video) available! You can remove any object from a video!\
[2023/4/24] [Local web UI](./app) supported! You can run the demo website locally!\
[2023/4/22] [Website](https://huggingface.co/spaces/InpaintAI/Inpaint-Anything) available! You can experience Inpaint Anything through the interface!\
[2023/4/22] [Remove Anything 3D](#remove-anything-3d) available! You can remove any 3D object from a 3D scene!\
[2023/4/13] [Technical report on arXiv](https://arxiv.org/abs/2304.06790) available!

## 🌟 Features
- [x] [**Remove** Anything](#remove-anything)
- [x] [**Fill** Anything](#fill-anything)
- [x] [**Replace** Anything](#replace-anything)
- [x] [Remove Anything **3D**](#remove-anything-3d) (<span style="color:red">🔥NEW</span>)
- [ ] Fill Anything **3D**
- [ ] Replace Anything **3D**
- [x] [Remove Anything **Video**](#remove-anything-video) (<span style="color:red">🔥NEW</span>)
- [ ] Fill Anything **Video**
- [ ] Replace Anything **Video**


## 💡 Highlights
- [x] Any aspect ratio supported
- [x] 2K resolution supported
- [x] [Technical report on arXiv](https://arxiv.org/abs/2304.06790) available (<span style="color:red">🔥NEW</span>)
- [x] [Website](https://huggingface.co/spaces/InpaintAI/Inpaint-Anything) available (<span style="color:red">🔥NEW</span>)
- [x] [Local web UI](./app) available (<span style="color:red">🔥NEW</span>)
- [x] Multiple modalities (i.e., image, video and 3D scene) supported (<span style="color:red">🔥NEW</span>)

<!-- ## Updates
| Date | News |
| ------ | --------
| 2023-04-12 | Release the Fill Anything feature | 
| 2023-04-10 | Release the Remove Anything feature |
| 2023-04-10 | Release the first version of Inpaint Anything | -->

## <span id="remove-anything">📌 Remove Anything</span>

**Click** on an object in the image, and Inpainting Anything will **remove** it instantly!
- Click on an object;
- [Segment Anything Model](https://segment-anything.com/) (SAM) segments the object out;
- Inpainting models (e.g., [LaMa](https://advimman.github.io/lama-project/)) fill the "hole".

### Installation
Requires `python>=3.8`
```bash
python -m pip install torch torchvision torchaudio
python -m pip install -e segment_anything
python -m pip install -r lama/requirements.txt 
```
In Windows, we recommend you to first install [miniconda](https://docs.conda.io/en/latest/miniconda.html) and 
open `Anaconda Powershell Prompt (miniconda3)` as administrator.
Then pip install [./lama_requirements_windows.txt](lama_requirements_windows.txt) instead of 
[./lama/requirements.txt](lama%2Frequirements.txt).

### Usage
Download the model checkpoints provided in [Segment Anything](./segment_anything/README.md) and [LaMa](./lama/README.md) (e.g., [sam_vit_h_4b8939.pth](https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth) and [big-lama](https://disk.yandex.ru/d/ouP6l8VJ0HpMZg)), and put them into `./pretrained_models`. For simplicity, you can also go [here](https://drive.google.com/drive/folders/1ST0aRbDRZGli0r7OVVOQvXwtadMCuWXg?usp=sharing), directly download [pretrained_models](https://drive.google.com/drive/folders/1wpY-upCo4GIW4wVPnlMh_ym779lLIG2A?usp=sharing), put the directory into `./` and get `./pretrained_models`.

For MobileSAM, the sam_model_type should use "vit_t", and the sam_ckpt should use "./weights/mobile_sam.pt".
For the MobileSAM project, please refer to [MobileSAM](https://github.com/ChaoningZhang/MobileSAM)
```
bash script/remove_anything.sh

```
Specify an image and a point, and Remove Anything will remove the object at the point.
```bash
python remove_anything.py \
    --input_img ./example/remove-anything/dog.jpg \
    --coords_type key_in \
    --point_coords 200 450 \
    --point_labels 1 \
    --dilate_kernel_size 15 \
    --output_dir ./results \
    --sam_model_type "vit_h" \
    --sam_ckpt ./pretrained_models/sam_vit_h_4b8939.pth \
    --lama_config ./lama/configs/prediction/default.yaml \
    --lama_ckpt ./pretrained_models/big-lama
```

**Click** on an object, **type** in what you want to fill, and Inpaint Anything will **fill** it!
- Click on an object;
- [SAM](https://segment-anything.com/) segments the object out;
- Input a text prompt;
- Text-prompt-guided inpainting models (e.g., [Stable Diffusion](https://github.com/CompVis/stable-diffusion)) fill the "hole" according to the text.

### Installation
Requires `python>=3.8`
```bash
python -m pip install torch torchvision torchaudio
python -m pip install -e segment_anything
python -m pip install diffusers transformers accelerate scipy safetensors
```

### Usage
Download the model checkpoints provided in [Segment Anything](./segment_anything/README.md) (e.g., [sam_vit_h_4b8939.pth](https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth)) and put them into `./pretrained_models`. For simplicity, you can also go [here](https://drive.google.com/drive/folders/1ST0aRbDRZGli0r7OVVOQvXwtadMCuWXg?usp=sharing), directly download [pretrained_models](https://drive.google.com/drive/folders/1wpY-upCo4GIW4wVPnlMh_ym779lLIG2A?usp=sharing), put the directory into `./` and get `./pretrained_models`.

For MobileSAM, the sam_model_type should use "vit_t", and the sam_ckpt should use "./weights/mobile_sam.pt".
For the MobileSAM project, please refer to [MobileSAM](https://github.com/ChaoningZhang/MobileSAM)
```
bash script/fill_anything.sh

```

Specify an image, a point and text prompt, and run:
```bash
python fill_anything.py \
    --input_img ./example/fill-anything/sample1.png \
    --coords_type key_in \
    --point_coords 750 500 \
    --point_labels 1 \
    --text_prompt "a teddy bear on a bench" \
    --dilate_kernel_size 50 \
    --output_dir ./results \
    --sam_model_type "vit_h" \
    --sam_ckpt ./pretrained_models/sam_vit_h_4b8939.pth
```

**Click** on an object, **type** in what background you want to replace, and Inpaint Anything will **replace** it!
- Click on an object;
- [SAM](https://segment-anything.com/) segments the object out;
- Input a text prompt;
- Text-prompt-guided inpainting models (e.g., [Stable Diffusion](https://github.com/CompVis/stable-diffusion)) replace the background according to the text.

### Installation
Requires `python>=3.8`
```bash
python -m pip install torch torchvision torchaudio
python -m pip install -e segment_anything
python -m pip install diffusers transformers accelerate scipy safetensors
```

### Usage
Download the model checkpoints provided in [Segment Anything](./segment_anything/README.md) (e.g. [sam_vit_h_4b8939.pth](https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth)) and put them into `./pretrained_models`. For simplicity, you can also go [here](https://drive.google.com/drive/folders/1ST0aRbDRZGli0r7OVVOQvXwtadMCuWXg?usp=sharing), directly download [pretrained_models](https://drive.google.com/drive/folders/1wpY-upCo4GIW4wVPnlMh_ym779lLIG2A?usp=sharing), put the directory into `./` and get `./pretrained_models`.

For MobileSAM, the sam_model_type should use "vit_t", and the sam_ckpt should use "./weights/mobile_sam.pt".
For the MobileSAM project, please refer to [MobileSAM](https://github.com/ChaoningZhang/MobileSAM)
```
bash script/replace_anything.sh

```

Specify an image, a point and text prompt, and run:
```bash
python replace_anything.py \
    --input_img ./example/replace-anything/dog.png \
    --coords_type key_in \
    --point_coords 750 500 \
    --point_labels 1 \
    --text_prompt "sit on the swing" \
    --output_dir ./results \
    --sam_model_type "vit_h" \
    --sam_ckpt ./pretrained_models/sam_vit_h_4b8939.pth
```

With a single **click** on an object in the *first* view of source views, Remove Anything 3D can remove the object from the *whole* scene!
- Click on an object in the first view of source views;
- [SAM](https://segment-anything.com/) segments the object out (with three possible masks);
- Select one mask;
- A tracking model such as [OSTrack](https://github.com/botaoye/OSTrack) is ultilized to track the object in these views;
- SAM segments the object out in each source view according to tracking results;
- An inpainting model such as [LaMa](https://advimman.github.io/lama-project/) is ultilized to inpaint the object in each source view.
- A novel view synthesizing model such as [NeRF](https://github.com/yenchenlin/nerf-pytorch) is ultilized to synthesize novel views of the scene without the object.

### Installation
Requires `python>=3.8`
```bash
python -m pip install torch torchvision torchaudio
python -m pip install -e segment_anything
python -m pip install -r lama/requirements.txt
python -m pip install jpeg4py lmdb
```

### Usage
Download the model checkpoints provided in [Segment Anything](./segment_anything/README.md) and [LaMa](./lama/README.md) (e.g., [sam_vit_h_4b8939.pth](https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth)), and put them into `./pretrained_models`. Further, download [OSTrack](https://github.com/botaoye/OSTrack) pretrained model from [here](https://drive.google.com/drive/folders/1ttafo0O5S9DXK2PX0YqPvPrQ-HWJjhSy) (e.g., [vitb_384_mae_ce_32x4_ep300.pth](https://drive.google.com/drive/folders/1XJ70dYB6muatZ1LPQGEhyvouX-sU_wnu)) and put it into `./pytracking/pretrain`. In addition, download [nerf_llff_data] (e.g, [horns](https://drive.google.com/drive/folders/1boi3eK8jNC8yv8IJ7lcL5_F1vutL3imc)), and put them into `./example/3d`. For simplicity, you can also go [here](https://drive.google.com/drive/folders/1ST0aRbDRZGli0r7OVVOQvXwtadMCuWXg?usp=sharing), directly download [pretrained_models](https://drive.google.com/drive/folders/1wpY-upCo4GIW4wVPnlMh_ym779lLIG2A?usp=sharing), put the directory into `./` and get `./pretrained_models`. Additionally, download [pretrain](https://drive.google.com/drive/folders/1SERTIfS7JYyOOmXWujAva4CDQf-W7fjv?usp=sharing), put the directory into `./pytracking` and get `./pytracking/pretrain`. 

For MobileSAM, the sam_model_type should use "vit_t", and the sam_ckpt should use "./weights/mobile_sam.pt".
For the MobileSAM project, please refer to [MobileSAM](https://github.com/ChaoningZhang/MobileSAM)
```
bash script/remove_anything_3d.sh

```
Specify a 3d scene, a point, scene config and mask index (indicating using which mask result of the first view), and Remove Anything 3D will remove the object from the whole scene.
```bash
python remove_anything_3d.py \
      --input_dir ./example/3d/horns \
      --coords_type key_in \
      --point_coords 830 405 \
      --point_labels 1 \
      --dilate_kernel_size 15 \
      --output_dir ./results \
      --sam_model_type "vit_h" \
      --sam_ckpt ./pretrained_models/sam_vit_h_4b8939.pth \
      --lama_config ./lama/configs/prediction/default.yaml \
      --lama_ckpt ./pretrained_models/big-lama \
      --tracker_ckpt vitb_384_mae_ce_32x4_ep300 \
      --mask_idx 1 \
      --config ./nerf/configs/horns.txt \
      --expname horns
```

With a single **click** on an object in the *first* video frame, Remove Anything Video can remove the object from the *whole* video!
- Click on an object in the first frame of a video;
- [SAM](https://segment-anything.com/) segments the object out (with three possible masks);
- Select one mask;
- A tracking model such as [OSTrack](https://github.com/botaoye/OSTrack) is ultilized to track the object in the video;
- SAM segments the object out in each frame according to tracking results;
- A video inpainting model such as [STTN](https://github.com/researchmm/STTN) is ultilized to inpaint the object in each frame.

### Installation
Requires `python>=3.8`
```bash
python -m pip install torch torchvision torchaudio
python -m pip install -e segment_anything
python -m pip install -r lama/requirements.txt
python -m pip install jpeg4py lmdb
```

### Usage
Download the model checkpoints provided in [Segment Anything](./segment_anything/README.md) and [STTN](./sttn/README.md) (e.g., [sam_vit_h_4b8939.pth](https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth) and [sttn.pth](https://drive.google.com/file/d/1ZAMV8547wmZylKRt5qR_tC5VlosXD4Wv/view)), and put them into `./pretrained_models`. Further, download [OSTrack](https://github.com/botaoye/OSTrack) pretrained model from [here](https://drive.google.com/drive/folders/1ttafo0O5S9DXK2PX0YqPvPrQ-HWJjhSy) (e.g., [vitb_384_mae_ce_32x4_ep300.pth](https://drive.google.com/drive/folders/1XJ70dYB6muatZ1LPQGEhyvouX-sU_wnu)) and put it into `./pytracking/pretrain`. For simplicity, you can also go [here](https://drive.google.com/drive/folders/1ST0aRbDRZGli0r7OVVOQvXwtadMCuWXg?usp=sharing), directly download [pretrained_models](https://drive.google.com/drive/folders/1wpY-upCo4GIW4wVPnlMh_ym779lLIG2A?usp=sharing), put the directory into `./` and get `./pretrained_models`. Additionally, download [pretrain](https://drive.google.com/drive/folders/1SERTIfS7JYyOOmXWujAva4CDQf-W7fjv?usp=sharing), put the directory into `./pytracking` and get `./pytracking/pretrain`.

For MobileSAM, the sam_model_type should use "vit_t", and the sam_ckpt should use "./weights/mobile_sam.pt".
For the MobileSAM project, please refer to [MobileSAM](https://github.com/ChaoningZhang/MobileSAM)
```
bash script/remove_anything_video.sh

```

Specify a video, a point, video FPS and mask index (indicating using which mask result of the first frame), and Remove Anything Video will remove the object from the whole video.
```bash
python remove_anything_video.py \
    --input_video ./example/video/paragliding/original_video.mp4 \
    --coords_type key_in \
    --point_coords 652 162 \
    --point_labels 1 \
    --dilate_kernel_size 15 \
    --output_dir ./results \
    --sam_model_type "vit_h" \
    --sam_ckpt ./pretrained_models/sam_vit_h_4b8939.pth \
    --lama_config lama/configs/prediction/default.yaml \
    --lama_ckpt ./pretrained_models/big-lama \
    --tracker_ckpt vitb_384_mae_ce_32x4_ep300 \
    --vi_ckpt ./pretrained_models/sttn.pth \
    --mask_idx 2 \
    --fps 25
```
https://raw.githubusercontent.com/geekyutao/Inpaint-Anything/refs/heads/main/remove_anything.py

import torch
import sys
import argparse
import numpy as np
from pathlib import Path
from matplotlib import pyplot as plt

from sam_segment import predict_masks_with_sam
from lama_inpaint import inpaint_img_with_lama
from utils import load_img_to_array, save_array_to_img, dilate_mask, \
    show_mask, show_points, get_clicked_point


def setup_args(parser):
    parser.add_argument(
        "--input_img", type=str, required=True,
        help="Path to a single input img",
    )
    parser.add_argument(
        "--coords_type", type=str, required=True,
        default="key_in", choices=["click", "key_in"], 
        help="The way to select coords",
    )
    parser.add_argument(
        "--point_coords", type=float, nargs='+', required=True,
        help="The coordinate of the point prompt, [coord_W coord_H].",
    )
    parser.add_argument(
        "--point_labels", type=int, nargs='+', required=True,
        help="The labels of the point prompt, 1 or 0.",
    )
    parser.add_argument(
        "--dilate_kernel_size", type=int, default=None,
        help="Dilate kernel size. Default: None",
    )
    parser.add_argument(
        "--output_dir", type=str, required=True,
        help="Output path to the directory with results.",
    )
    parser.add_argument(
        "--sam_model_type", type=str,
        default="vit_h", choices=['vit_h', 'vit_l', 'vit_b', 'vit_t'],
        help="The type of sam model to load. Default: 'vit_h"
    )
    parser.add_argument(
        "--sam_ckpt", type=str, required=True,
        help="The path to the SAM checkpoint to use for mask generation.",
    )
    parser.add_argument(
        "--lama_config", type=str,
        default="./lama/configs/prediction/default.yaml",
        help="The path to the config file of lama model. "
             "Default: the config of big-lama",
    )
    parser.add_argument(
        "--lama_ckpt", type=str, required=True,
        help="The path to the lama checkpoint.",
    )


if __name__ == "__main__":
    """Example usage:
    python remove_anything.py \
        --input_img FA_demo/FA1_dog.png \
        --coords_type key_in \
        --point_coords 750 500 \
        --point_labels 1 \
        --dilate_kernel_size 15 \
        --output_dir ./results \
        --sam_model_type "vit_h" \
        --sam_ckpt sam_vit_h_4b8939.pth \
        --lama_config lama/configs/prediction/default.yaml \
        --lama_ckpt big-lama 
    """
    parser = argparse.ArgumentParser()
    setup_args(parser)
    args = parser.parse_args(sys.argv[1:])
    device = "cuda" if torch.cuda.is_available() else "cpu"

    if args.coords_type == "click":
        latest_coords = get_clicked_point(args.input_img)
    elif args.coords_type == "key_in":
        latest_coords = args.point_coords
    img = load_img_to_array(args.input_img)

    masks, _, _ = predict_masks_with_sam(
        img,
        [latest_coords],
        args.point_labels,
        model_type=args.sam_model_type,
        ckpt_p=args.sam_ckpt,
        device=device,
    )
    masks = masks.astype(np.uint8) * 255

    # dilate mask to avoid unmasked edge effect
    if args.dilate_kernel_size is not None:
        masks = [dilate_mask(mask, args.dilate_kernel_size) for mask in masks]

    # visualize the segmentation results
    img_stem = Path(args.input_img).stem
    out_dir = Path(args.output_dir) / img_stem
    out_dir.mkdir(parents=True, exist_ok=True)
    for idx, mask in enumerate(masks):
        # path to the results
        mask_p = out_dir / f"mask_{idx}.png"
        img_points_p = out_dir / f"with_points.png"
        img_mask_p = out_dir / f"with_{Path(mask_p).name}"

        # save the mask
        save_array_to_img(mask, mask_p)

        # save the pointed and masked image
        dpi = plt.rcParams['figure.dpi']
        height, width = img.shape[:2]
        plt.figure(figsize=(width/dpi/0.77, height/dpi/0.77))
        plt.imshow(img)
        plt.axis('off')
        show_points(plt.gca(), [latest_coords], args.point_labels,
                    size=(width*0.04)**2)
        plt.savefig(img_points_p, bbox_inches='tight', pad_inches=0)
        show_mask(plt.gca(), mask, random_color=False)
        plt.savefig(img_mask_p, bbox_inches='tight', pad_inches=0)
        plt.close()

    # inpaint the masked image
    for idx, mask in enumerate(masks):
        mask_p = out_dir / f"mask_{idx}.png"
        img_inpainted_p = out_dir / f"inpainted_with_{Path(mask_p).name}"
        img_inpainted = inpaint_img_with_lama(
            img, mask, args.lama_config, args.lama_ckpt, device=device)
        save_array_to_img(img_inpainted, img_inpainted_p)

Task: Create scripts/diffueraser_worker.py

PUBLIC FUNCTION:
def run_diffueraser_job(job_id, video_path, mask_b64, start, end) -> str

All paths via os.environ.get() — no hardcoded paths.

STEP 1 — Frame extraction
- ffprobe rotation detection (tags.rotate + side_data displaymatrix)
- ffmpeg with -noautorotate before -i
- transpose=2 for -90°, transpose=1 for 90°, vflip,hflip for 180°
- Frames to: Path(os.environ.get("OUTPUT_DIR","output")) / "inpaint_temp" / job_id / "frames"
- Status: { status: "extracting", progress: 0.05 }

STEP 2 — SAM2 mask propagation
- Load from os.environ["SAM2_CHECKPOINT"]
- Decode mask_b64 → find centroid → point prompt on frame 0
- propagate_in_video() all frames
- CRITICAL: del predictor; torch.cuda.empty_cache() before Step 3
- Masks to: .../inpaint_temp/<job_id>/masks/
- Status: { status: "masking", progress: 0.25 }

STEP 3 — DiffuEraser
- sys.path.insert(0, os.environ.get("DIFFUERASER_DIR","/workspace/DiffuEraser"))
- Load using ACTUAL API from pasted README
- 960x540 resolution, OOM retry at 640x360
- Status: 0.25 → 0.90

STEP 4 — Output
- Copy to output/inpainted/<job_id>.mp4
- Status: { status: "done", progress: 1.0, output_path: "..." }
- Cleanup temp

STATUS JSON: output/inpaint_jobs/<job_id>.json
Format: { status, progress, frames_done, frames_total, estimated_seconds, output_path, error }

OOM handling: catch RuntimeError, retry lower res, write warning in status.
All exceptions: write failed status.

__main__ block:
python3 -m scripts.diffueraser_worker --video <p> --mask <m> --start 0 --end 3 --job_id test

After writing: python3 -m py_compile scripts/diffueraser_worker.py
```

---

### STEP 5 — Wire into app.py

**Prompt for Claude Code:**

```
Task: Add "diffueraser" as default engine in app.py api_inpaint_start().

1. Add "diffueraser" to _VALID_ENGINES
2. Pre-flight: check os.environ.get("DIFFUERASER_DIR") exists, return 501 if not
3. In _run(): add diffueraser branch importing run_diffueraser_job
4. Change default: body.get("engine", "lama") → body.get("engine", "diffueraser")

No other changes. python3 -m py_compile app.py after.
```

---

### STEP 6 — Update React UI

**Prompt for Claude Code:**

```
Update InpaintTab.tsx and api.ts:

api.ts: add "diffueraser" as first option in InpaintEngine type

InpaintTab.tsx:
- Default engine: "diffueraser"
- 3-button selector:
  "DiffuEraser" (default, green) — "~3 min · Best quality"
  "LaMa" — "~1 min · Fast"
  "LaMa + E2FGVI" — "~5 min · Smooth"
- Subtitle: "Powered by DiffuEraser · Removes people and reconstructs background"

No changes to mask drawing, polling, or result display.
cd Web/video_agent && npm run build — report errors.
```

---

### STEP 7 — Smoke test

```bash
# Terminal 1
cd /workspace/videoagent && source .venv/bin/activate && python3 app.py

# Terminal 2
python3 -c "
import numpy as np, cv2
mask = np.zeros((1920,1080), dtype=np.uint8)
cv2.circle(mask, (540,1600), 200, 255, -1)
cv2.imwrite('/tmp/smoke_mask.png', mask)
print('Mask created')
"
python3 -m scripts.diffueraser_worker \
  --video raw_clips/$(ls raw_clips/ | head -1) \
  --mask /tmp/smoke_mask.png --start 0 --end 3 --job_id smoke

cat output/inpaint_jobs/smoke.json
ls -lh output/inpainted/smoke.mp4
```

---

### STEP 8 — Quality tuning

Browser test via tunnel: `ssh -p 27484 -L 5100:localhost:5100 root@213.224.31.105 -N` → `http://localhost:5100`

Draw mask with 20-30px padding around the person. If output needs adjustment:

```
DiffuEraser result shows [describe: flickering/edge halo/wrong area].
Look at scripts/diffueraser_worker.py. Minimum targeted fix only.
Check: mask dilation (cv2.dilate 15px), resolution, guidance_scale, inference steps.
Report before changing anything.
```

---

### STEP 9 — Drop-in upgrades (after Step 7 passes)

**9A Faster-Whisper:** Replace Whisper in whisper_helper.py. Same signatures. `pip install faster-whisper`. Model: large-v3, cuda, float16.

**9B Aesthetic Predictor V2.5:** Replace LAION aesthetics in semantic_aesthetic.py. `pip install aesthetic-predictor-v2-5`. Same signatures.

**9C auto-editor pre-filter:** Add to analyze_and_edit.py before PySceneDetect. `pip install auto-editor`. Command: `auto-editor <clip> --edit "audio:threshold=3% or motion:threshold=6%" --export json`. Graceful degradation if missing.

---

## GitHub Workflow

```bash
# After changes on Windows:
cd D:\video-agent
git add -A
git commit -m "feat: DiffuEraser pipeline + cross-platform portability"
git push origin main

# Pull updates on server:
cd /workspace/videoagent && git pull origin main
source .venv/bin/activate
cd Web/video_agent && npm run build && cd ../..
python3 app.py

# Fresh Vast.ai instance from scratch:
ssh -p <PORT> root@<IP>
git clone https://github.com/<YOUR_USERNAME>/video-agent.git /workspace/videoagent
cd /workspace/videoagent
chmod +x deploy_vastai.sh
./deploy_vastai.sh 2>&1 | tee /workspace/deploy.log
nano .env  # fill in any keys
scp -P <PORT> -r "D:\video-agent\raw_clips\*" root@<IP>:/workspace/videoagent/raw_clips/
source .venv/bin/activate && python3 app.py
```

---

## Final File Structure

```
D:\video-agent\ (GitHub repo)
├── app.py                             ← updated: diffueraser default
├── .env.example                       ← NEW: committed template
├── .env                               ← NOT committed
├── .gitignore                         ← updated
├── deploy_vastai.sh                   ← NEW: one-command server setup
├── requirements.txt                   ← updated
├── CLAUDE.md                          ← updated: portability rules
├── scripts/
│   ├── diffueraser_worker.py          ← NEW: primary inpaint engine
│   ├── lama_worker.py                 ← secondary engine
│   ├── e2fgvi_worker.py               ← secondary engine
│   ├── sam_helper.py                  ← unchanged
│   ├── analyze_and_edit.py            ← Step 9C: auto-editor
│   ├── whisper_helper.py              ← Step 9A: faster-whisper
│   └── semantic_aesthetic.py          ← Step 9B: aesthetic predictor v2.5
└── Web/video_agent/src/
    └── components/InpaintTab.tsx      ← updated: DiffuEraser default

/workspace/ (server only, not in repo)
├── DiffuEraser/                       ← installed by deploy script
├── sam2_checkpoints/                  ← installed by deploy script
└── stable-diffusion-v1-5/            ← installed by deploy script
```

---

## VRAM Budget

| Model | VRAM | When |
|---|---|---|
| SigLIP | ~2 GB | Always |
| Faster-Whisper large-v3 | ~3 GB | Always |
| SAM2 large | ~10 GB | Step 2 — unloaded before Step 3 |
| DiffuEraser @ 640×360 | ~12 GB | Step 3 |
| DiffuEraser @ 960×540 | ~20 GB | Step 3 |
| Peak sequential | ~15 GB | SAM2 unloaded first |

---

## Risks

| Risk | Mitigation |
|---|---|
| DiffuEraser API differs from docs | Read inference_diffueraser.py first, paste to Claude Code |
| SAM2 wrong mask on fast subjects | Mask dilation + user can Redraw |
| OOM at 960×540 | Auto-fallback to 640×360 in worker |
| DiffuEraser pip conflicts | Check requirements.txt before install |
| Rotation bug reappears | Always -noautorotate + transpose=2 in every ffmpeg call |
| Windows path in code breaks Linux | Step 0 audit + env var rule |
| .env accidentally committed | .gitignore + warning in deploy script |

---

## Session Handoff Prompt

```
Read CLAUDE.md and SESSIONS.md first.

Context: VideoAgent inpainting migration.
LOCAL: D:\video-agent | SERVER: /workspace/videoagent | REPO: GitHub

Check and report DONE or PENDING:
1. .env.example at repo root?
2. .gitignore excludes .env, raw_clips/, output/, style/*.pth, dist/?
3. deploy_vastai.sh at repo root?
4. scripts/diffueraser_worker.py exists?
5. app.py routes "diffueraser" as default engine?
6. InpaintTab.tsx shows DiffuEraser as default?
7. /workspace/DiffuEraser cloned + weights present? (server)
8. /workspace/sam2_checkpoints/sam2_hiera_large.pt exists? (server)
9. .env has SAM2_CHECKPOINT + DIFFUERASER_DIR? (server)

Report status. Ask which step to continue. No changes until confirmed.
```
