# Vast.ai Instance Setup
## From zero to ready for deploy_vastai.sh

---

## Step 1 — Choosing Your Instance on Vast.ai

### GPU
**RTX 3090 (24GB VRAM)** — stick with what works. Confirmed compatible with your stack.

If RTX 3090 is unavailable or expensive, acceptable alternatives in order:
- RTX 4090 (24GB) — faster, ~2x price, same VRAM
- RTX A5000 (24GB) — data centre grade, more stable
- RTX 3090 Ti (24GB) — same as 3090 effectively

**Avoid:** anything with less than 20GB VRAM. DiffuEraser at 960x540 needs ~20GB.

### Disk
**Minimum: 60GB** — this is the critical change from your previous 16GB instance.

Breakdown of what will be on disk:
```
DiffuEraser weights:          ~8 GB
Stable Diffusion 1.5:         ~4 GB
SAM2 large checkpoint:        ~1 GB
SAM ViT-B checkpoint:         ~0.4 GB
Python venv + packages:       ~6 GB
VideoAgent repo + React build: ~2 GB
Raw clips (your footage):     ~5-15 GB (varies)
Output / working temp:        ~10 GB headroom
──────────────────────────────────────
Total needed:                 ~37-50 GB
Recommended order:            60 GB
```

On Vast.ai the disk field is called **"Disk Space"** — set it to **60 GB** minimum when filtering instances.

### RAM
**32GB minimum.** DiffuEraser loads large tensors into CPU RAM during pipeline setup.

### CPU
**4+ vCPUs.** ffmpeg frame extraction and React build both use CPU. More is better but not critical.

### Docker Image
Select: **`vastai/pytorch_cuda-12.1-auto`** or **`vastai/pytorch`** with CUDA 12.1+

Your previous instance used `cuda-12.6.3-auto` and that worked fine. Anything 12.1+ is fine.

**Do NOT select** Jupyter-only images — you need SSH access.

### Network
- **SSH enabled:** Yes (required)
- **Open ports:** Add port 5100 (Flask) when configuring

### What to look for in the instance list
```
GPU:        RTX 3090
VRAM:       24 GB
Disk:       60+ GB
RAM:        32+ GB
vCPUs:      4+
CUDA:       12.1+
SSH:        Yes
Price:      $0.15 - $0.25/hr is typical for RTX 3090
```

---

## Step 2 — Launch the Instance

1. Go to https://cloud.vast.ai
2. Filter: GPU = RTX 3090, Disk > 60GB, RAM > 32GB
3. Sort by price
4. Click **Rent** on your chosen instance
5. Under **Instance Configuration**:
   - Docker image: `vastai/pytorch` (latest CUDA 12.x)
   - Disk: set to 60 GB
   - Open ports: add `5100/tcp`
6. Click **Create**
7. Wait ~2 minutes for it to start
8. Note down: **IP address** and **SSH port** (will NOT be 22 — it'll be something like 27484)

---

## Step 3 — Connect via SSH

**From Windows PowerShell:**

```powershell
ssh -p <YOUR_PORT> root@<YOUR_IP>
```

Example from your previous session:
```powershell
ssh -p 27484 root@213.224.31.105
```

Accept the fingerprint prompt (type `yes`).

**If you have an SSH key configured on Vast.ai** (recommended):
- Key auth works automatically
- If not, Vast.ai will show a password in the instance dashboard

---

## Step 4 — First Commands After Login

Run these in order. Each one verifies the instance is healthy before you proceed.

### 4.1 — Verify GPU
```bash
nvidia-smi
```
Expected output: RTX 3090, 24576 MiB, CUDA 12.x
If this fails the instance is broken — destroy and try another.

### 4.2 — Verify disk space
```bash
df -h /
```
You need at least 55GB available. If you only see 16GB, the disk was not set correctly — destroy and re-create with 60GB disk.

### 4.3 — Verify CUDA and PyTorch
```bash
python3 -c "import torch; print('CUDA:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0)); print('VRAM:', round(torch.cuda.get_device_properties(0).total_memory/1e9, 1), 'GB')"
```
Expected: CUDA: True, GPU: NVIDIA GeForce RTX 3090, VRAM: 24.0 GB

### 4.4 — Verify ffmpeg
```bash
ffmpeg -version | head -1
```
Expected: ffmpeg version 4.x or 6.x — any version is fine.
If missing: `apt-get install -y ffmpeg`

### 4.5 — Verify Python version
```bash
python3 --version
```
Expected: Python 3.10, 3.11, or 3.12 — all work.

### 4.6 — Verify Node.js
```bash
node --version
npm --version
```
Expected: Node 18+ and npm 9+
If missing:
```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs
```

### 4.7 — Check internet speed (optional but useful)
```bash
curl -s https://raw.githubusercontent.com/sivel/speedtest-cli/master/speedtest.py | python3 -
```
You want at least 200 Mbps download — the DiffuEraser + SD 1.5 downloads are ~12GB total.

---

## Step 5 — Install Rust (required before deploy script)

The deploy script installs Rust itself, but if you want to pre-verify it works:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
source $HOME/.cargo/env
rustc --version
```
Expected: rustc 1.7x.x

---

## Step 6 — Set up tmux (so Flask survives SSH disconnects)

```bash
# Install if not present
apt-get install -y tmux

# Create a persistent session
tmux new-session -d -s videoagent
tmux split-window -h -t videoagent

# Verify
tmux ls
```

You'll use this later to run Flask in one pane and monitor in another.

---

## Step 7 — Clone your GitHub repo

```bash
cd /workspace
git clone https://github.com/vinal-2/video_agent.git videoagent
cd videoagent
ls
```

You should see: `app.py`, `scripts/`, `Web/`, `deploy_vastai.sh`, `.env.example`, `requirements.txt`

If `deploy_vastai.sh` is not there yet (you haven't committed it from Step 0 of the migration), clone it later after completing Step 0 on Windows first.

**Temporary workaround if repo isn't ready yet:**
```bash
# Clone just to get the structure, then pull when ready
git clone https://github.com/<YOUR_USERNAME>/<YOUR_REPO>.git videoagent
cd videoagent
# Work through migration Step 0 on Windows, push, then:
git pull origin main
```

---

## Step 8 — Create .env from template

```bash
cd /workspace/videoagent
cp .env.example .env
```

The `.env.example` file already has correct `/workspace/...` defaults for Vast.ai — no path editing needed. The only things worth changing are LLM-related:

```bash
nano .env
```

Find and set these two lines (Vast.ai has no LM Studio by default):
```
ENABLE_LLM_PLANNER=0
VISION_TAGGER_MODEL=none
```

Everything else (`PROPAINTER_DIR`, `DIFFUERASER_DIR`, `STABLE_DIFFUSION_DIR`, `SAM2_CHECKPOINTS_DIR`, etc.) already points to the correct `/workspace/...` paths.

Save: `Ctrl+X` → `Y` → `Enter`

To verify the file looks right:
```bash
grep -v "^#" .env | grep -v "^$"
```

---

## Step 9 — Create required directories

```bash
mkdir -p /workspace/videoagent/raw_clips
mkdir -p /workspace/videoagent/output/inpainted
mkdir -p /workspace/videoagent/output/inpaint_jobs
mkdir -p /workspace/videoagent/output/inpaint_temp
mkdir -p /workspace/videoagent/logs
mkdir -p /workspace/sam2_checkpoints
```

---

## Step 10 — Upload your raw clips

**From Windows PowerShell** (run this on your laptop, not the server):

```powershell
scp -P 22239 -r "D:\video-agent\raw_clips\*" root@185.227.170.193:/workspace/videoagent/raw_clips/
```
scp -P 22239 "D:\video-agent\raw_clips\*" ssh -p 22239 root@185.227.170.193 -L 8080:localhost:8080

This can take a while depending on clip size. Run it in a separate PowerShell window so it doesn't block you.

Verify on server:
```bash
ls -lh /workspace/videoagent/raw_clips/
```

---

## Step 11 — Run deploy_vastai.sh

You are now ready. Run the deploy script:

```bash
cd /workspace/videoagent
chmod +x deploy_vastai.sh
./deploy_vastai.sh 2>&1 | tee /workspace/deploy.log
```

This will take **15-30 minutes** depending on download speeds (SD 1.5 + DiffuEraser weights = ~12GB).

Monitor progress:
```bash
# In a second SSH session or tmux pane:
tail -f /workspace/deploy.log
```

When it finishes, check the summary at the bottom — all steps should show PASS.

---

## Step 12 — Verify deploy completed

```bash
# Check key outputs
ls -lh /workspace/DiffuEraser/weights/        # DiffuEraser weights
ls -lh /workspace/sam2_checkpoints/           # SAM2 checkpoint
ls -lh /workspace/videoagent/style/           # SAM ViT-B
ls -lh /workspace/videoagent/Web/video_agent/dist/  # React build
ls -lh /workspace/stable-diffusion-v1-5/     # SD 1.5

# Verify venv
source /workspace/videoagent/.venv/bin/activate
python3 -c "import torch, flask, cv2, numpy, PIL; print('Core imports OK')"
python3 -c "from sam2.build_sam import build_sam2_video_predictor; print('SAM2 OK')"
```

---

## Step 13 — Start Flask and verify UI

```bash
# Attach to tmux session
tmux attach -t videoagent

# In pane 1:
cd /workspace/videoagent
source .venv/bin/activate
python3 app.py
```

You should see: `Running on http://0.0.0.0:5100`

**From Windows — open SSH tunnel:**
```powershell
ssh -p <YOUR_PORT> -L 5100:localhost:5100 root@<YOUR_IP> -N
```

Open browser: `http://localhost:5100`

You should see the VideoAgent UI. If the Inpaint tab shows DiffuEraser as the default engine — setup is complete.

---

## Quick Reference — Commands You'll Use Daily

```bash
# SSH in
ssh -p <PORT> root@<IP>

# SSH tunnel for browser
ssh -p <PORT> -L 5100:localhost:5100 root@<IP> -N

# Upload clips from Windows (run on Windows)
scp -P <PORT> -r "D:\video-agent\raw_clips\*" root@<IP>:/workspace/videoagent/raw_clips/

# Attach to tmux
tmux attach -t videoagent

# Start Flask (inside tmux)
cd /workspace/videoagent && source .venv/bin/activate && python3 app.py

# Pull latest code from GitHub
cd /workspace/videoagent && git pull origin main

# Rebuild React after frontend changes
cd /workspace/videoagent/Web/video_agent && npm run build

# Monitor GPU
watch -n 1 nvidia-smi

# Check disk
df -h /workspace

# Check inpaint job status
cat /workspace/videoagent/output/inpaint_jobs/<job_id>.json

# View deploy log
tail -100 /workspace/deploy.log
```

---

## Cost Control

```
RTX 3090 on Vast.ai:  ~$0.15-0.25/hr
8 hours active use:   ~$1.60
Left running 30 days: ~$108-180  ← always stop when not using

STOP the instance when done:
- Vast.ai dashboard → Instance → Stop (not Destroy — Destroy deletes all data)
- Stop pauses billing
- Restart resumes from exactly where you left off (all files preserved)
- Only Destroy if you want to start completely fresh
```

---

## Troubleshooting Common Issues

| Problem | Fix |
|---|---|
| nvidia-smi not found | Wrong image selected — destroy and use pytorch CUDA image |
| Only 16GB disk | Disk not set at create time — destroy and re-create with 60GB |
| SSH connection refused | Wait 2 more minutes, instance still booting |
| Port 5100 not accessible | Check Vast.ai instance open ports — add 5100/tcp |
| deploy.log shows FAIL on Rust | Run Rust install manually (Step 5 above) then re-run deploy |
| deploy.log shows FAIL on DiffuEraser weights | Re-run just the weights download — likely network timeout |
| Flask starts but UI is blank | React build didn't complete — run `npm run build` manually |
| OOM during inpaint | DiffuEraser falling back to 640x360 automatically — normal |
