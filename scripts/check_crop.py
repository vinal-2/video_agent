"""
check_crop.py
-------------
Phase 1 diagnostic — verifies smart_crop.compute_auto_crop() against every
clip in raw_clips/. Run from D:\\video-agent\\scripts\\ with the .venv active.

Usage:
    python check_crop.py
    python check_crop.py --clip ../raw_clips/1000142794.mp4
"""

import argparse
import json
import sys
from pathlib import Path

# Allow imports from the project root (one level up from scripts/)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import cv2
except ImportError:
    sys.exit("ERROR: opencv-python not installed. Run: pip install opencv-python")

try:
    from scripts.smart_crop import compute_auto_crop
except ImportError as e:
    sys.exit(f"ERROR: Could not import smart_crop: {e}")


CLIPS_DIR = ROOT / "raw_clips"

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"
INFO = "\033[94mINFO\033[0m"


def probe_clip(path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    cap.release()
    return {"w": w, "h": h, "fps": fps, "dur": dur}


def check_clip(path):
    print(f"\n{'─'*60}")
    print(f"Clip : {path.name}")

    info = probe_clip(path)
    if not info:
        print(f"  [{FAIL}] Could not open clip with cv2")
        return False

    w, h, fps, dur = info["w"], info["h"], info["fps"], info["dur"]
    is_portrait      = h > w
    expected_crop_w  = int(h * 9 / 16)

    print(f"  [{INFO}] Source  : {w}x{h}  ({'portrait' if is_portrait else 'LANDSCAPE'})  {dur:.1f}s  {fps:.0f}fps")
    print(f"  [{INFO}] Expected crop_w = {h} * 9/16 = {expected_crop_w}px")

    if is_portrait:
        print(f"  [{SKIP}] Crop tool should be HIDDEN (already portrait) — testing formula anyway")

    test_end = min(3.0, dur)
    try:
        result = compute_auto_crop(str(path), 0, test_end)
    except Exception as e:
        print(f"  [{FAIL}] compute_auto_crop raised an exception: {e}")
        return False

    print(f"  [{INFO}] Result  : {json.dumps(result)}")

    ok = True

    # 1. Required keys
    for key in ("x", "y", "w", "h", "source_w", "source_h"):
        if key not in result:
            print(f"  [{FAIL}] Missing key: '{key}'")
            ok = False
    if not ok:
        return False

    # 2. source dimensions match cv2
    if result["source_w"] != w or result["source_h"] != h:
        print(f"  [{FAIL}] source dims: got {result['source_w']}x{result['source_h']}, expected {w}x{h}")
        ok = False
    else:
        print(f"  [{PASS}] source_w / source_h match cv2")

    # 3. crop h == source h
    if result["h"] != h:
        print(f"  [{FAIL}] crop h={result['h']} should equal source_h={h}")
        ok = False
    else:
        print(f"  [{PASS}] crop h equals source_h")

    # 4. crop w == floor(source_h * 9/16), ±2px rounding tolerance
    if abs(result["w"] - expected_crop_w) > 2:
        print(f"  [{FAIL}] crop w={result['w']} but expected {expected_crop_w}")
        print(f"          ^ Formula is probably using source_w instead of source_h")
        ok = False
    else:
        print(f"  [{PASS}] crop w={result['w']} ≈ expected {expected_crop_w} (h*9/16)")

    # 5. x clamp: x + crop_w <= source_w
    if result["x"] + result["w"] > w:
        print(f"  [{FAIL}] clamp broken: x={result['x']} + w={result['w']} = {result['x']+result['w']} > source_w={w}")
        ok = False
    else:
        print(f"  [{PASS}] x clamp OK ({result['x']} + {result['w']} = {result['x']+result['w']} <= {w})")

    # 6. y must be 0
    if result["y"] != 0:
        print(f"  [{FAIL}] y={result['y']} should always be 0")
        ok = False
    else:
        print(f"  [{PASS}] y = 0")

    # 7. portrait clips: x should be 0
    if is_portrait and result["x"] != 0:
        print(f"  [{FAIL}] portrait clip: x={result['x']} should be 0")
        ok = False

    return ok


def main():
    parser = argparse.ArgumentParser(description="Verify smart_crop against raw clips")
    parser.add_argument("--clip", type=str,
                        help="Test a single clip (path relative to raw_clips/ or absolute)")
    args = parser.parse_args()

    print("=" * 60)
    print("  Phase 1 — smart_crop diagnostic")
    print("=" * 60)

    if args.clip:
        p = Path(args.clip)
        if not p.is_absolute():
            p = CLIPS_DIR / p
        clips = [p]
    else:
        clips = sorted(CLIPS_DIR.glob("*.mp4")) + sorted(CLIPS_DIR.glob("*.mov"))

    if not clips:
        sys.exit(f"No clips found in {CLIPS_DIR}")

    print(f"\n  Testing {len(clips)} clip(s) from {CLIPS_DIR}\n")

    results = []
    for clip in clips:
        ok = check_clip(clip)
        results.append((clip.name, ok))

    # Summary
    print(f"\n{'='*60}")
    print("  Summary")
    print(f"{'='*60}")
    passed = sum(1 for _, ok in results if ok)
    failed = len(results) - passed

    for name, ok in results:
        marker = PASS if ok else FAIL
        print(f"  [{marker}] {name}")

    print(f"\n  {passed}/{len(results)} passed")

    if failed:
        print(f"\n  {failed} clip(s) failed.")
        print("  Fix smart_crop.py before proceeding to the API endpoint test.")
        sys.exit(1)
    else:
        print("\n  All checks passed.")
        print("  Next step: test POST /api/crop_auto with Flask running.")


if __name__ == "__main__":
    main()