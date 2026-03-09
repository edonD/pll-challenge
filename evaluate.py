"""
PLL Challenge Evaluation Script.
DO NOT MODIFY THIS FILE.

Runs ngspice simulation, extracts metrics, computes score.
Usage: python evaluate.py [--timeout 300]
"""

import subprocess
import re
import sys
import os
import time
import json
import math
import argparse

# ---------------------------------------------------------------------------
# Specs (target values)
# ---------------------------------------------------------------------------
SPECS = {
    "lock_time_us":     {"target": 50,    "weight": 0.25, "direction": "lower",  "unit": "us"},
    "ctrl_ripple_mv":   {"target": 5,     "weight": 0.20, "direction": "lower",  "unit": "mV"},
    "phase_noise_dBc":  {"target": -90,   "weight": 0.25, "direction": "lower",  "unit": "dBc/Hz@1MHz"},
    "ref_spur_dBc":     {"target": -60,   "weight": 0.15, "direction": "lower",  "unit": "dBc"},
    "power_mw":         {"target": 5,     "weight": 0.10, "direction": "lower",  "unit": "mW"},
    "stability":        {"target": 1.0,   "weight": 0.05, "direction": "higher", "unit": "bool"},
}

NGSPICE_CMD = "ngspice"
DESIGN_FILE = "design.cir"
METRICS_FILE = "metrics.txt"
RESULTS_FILE = "results.jsonl"


def find_ngspice():
    """Find ngspice binary."""
    # Check common locations
    paths = [
        "ngspice",
        "/usr/bin/ngspice",
        "/usr/local/bin/ngspice",
        os.path.expanduser("~/ngspice/bin/ngspice"),
    ]
    for p in paths:
        try:
            r = subprocess.run([p, "--version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return p
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return "ngspice"  # hope it's on PATH


def run_simulation(timeout=300):
    """Run ngspice on design.cir and return (success, runtime, stdout, stderr)."""
    ngspice = find_ngspice()
    t0 = time.time()
    try:
        result = subprocess.run(
            [ngspice, "-b", DESIGN_FILE],
            capture_output=True,
            timeout=timeout,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        runtime = time.time() - t0
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        return result.returncode == 0, runtime, stdout, stderr
    except subprocess.TimeoutExpired:
        return False, timeout, "", f"TIMEOUT after {timeout}s"
    except FileNotFoundError:
        return False, 0, "", f"ngspice not found at '{ngspice}'"


def parse_metrics(stdout, stderr):
    """Extract metrics from ngspice output and metrics.txt."""
    metrics = {}
    combined = stdout + "\n" + stderr

    # Parse from metrics.txt if it exists
    metrics_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), METRICS_FILE)
    if os.path.exists(metrics_path):
        with open(metrics_path) as f:
            for line in f:
                m = re.match(r"(\w+):\s*([\d.eE+-]+)", line.strip())
                if m:
                    try:
                        metrics[m.group(1)] = float(m.group(2))
                    except ValueError:
                        pass

    # Also parse meas results from stdout/stderr
    for line in combined.split("\n"):
        # ngspice .meas output format: "name = value"
        m = re.match(r"\s*(\w+)\s*=\s*([\d.eE+-]+)", line)
        if m:
            try:
                metrics[m.group(1)] = float(m.group(2))
            except ValueError:
                pass

    return metrics


def analyze_waveforms():
    """Analyze saved waveform data for additional metrics."""
    extra = {}
    ctrl_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ctrl_waveform.txt")

    if os.path.exists(ctrl_file):
        times, values = [], []
        with open(ctrl_file) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 2:
                    try:
                        t = float(parts[0])
                        v = float(parts[1])
                        times.append(t)
                        values.append(v)
                    except ValueError:
                        continue

        if len(values) > 100:
            # Check stability: is ctrl voltage monotonically settling?
            # Look at last 10% of data
            n = len(values)
            tail = values[int(0.9 * n):]
            if tail:
                mean_tail = sum(tail) / len(tail)
                var_tail = sum((v - mean_tail)**2 for v in tail) / len(tail)
                std_tail = var_tail ** 0.5
                extra["ctrl_std_mv"] = std_tail * 1000  # mV
                extra["ctrl_settled"] = 1.0 if std_tail < 0.01 else 0.0  # settled if < 10mV std

                # Detect ringing: count zero-crossings of (ctrl - mean) in last 20%
                tail20 = values[int(0.8 * n):]
                mean20 = sum(tail20) / len(tail20)
                crossings = sum(1 for i in range(1, len(tail20))
                               if (tail20[i] - mean20) * (tail20[i-1] - mean20) < 0)
                extra["zero_crossings"] = crossings
                extra["is_stable"] = 1.0 if crossings < 20 else 0.0

            # Estimate lock time: first time ctrl enters 1% band and stays
            if len(values) > 10:
                final_val = sum(values[-50:]) / min(50, len(values))
                band = abs(final_val) * 0.01 if final_val != 0 else 0.01
                lock_idx = None
                for i in range(len(values)):
                    if abs(values[i] - final_val) < band:
                        # Check it stays within band
                        stayed = True
                        for j in range(i, min(i + 100, len(values))):
                            if abs(values[j] - final_val) > band * 2:
                                stayed = False
                                break
                        if stayed:
                            lock_idx = i
                            break
                if lock_idx is not None and lock_idx < len(times):
                    extra["lock_time_from_waveform"] = times[lock_idx]

    return extra


def compute_score(raw_metrics):
    """
    Compute a 0-1 score from raw simulation metrics.
    Higher is better. 1.0 = all specs met perfectly.
    """
    scores = {}

    # Lock time
    lock_time_us = raw_metrics.get("lock_time", raw_metrics.get("lock_time_from_waveform", 999)) * 1e6
    if lock_time_us <= 0:
        lock_time_us = 999
    target = SPECS["lock_time_us"]["target"]
    scores["lock_time"] = min(1.0, target / lock_time_us) if lock_time_us > 0 else 0.0

    # Control voltage ripple
    ripple_mv = raw_metrics.get("ctrl_ripple", 999) * 1000
    target = SPECS["ctrl_ripple_mv"]["target"]
    scores["ctrl_ripple"] = min(1.0, target / ripple_mv) if ripple_mv > 0 else 0.0

    # Phase noise (placeholder - estimated from ctrl ripple and VCO params)
    # Real phase noise requires FFT analysis; approximate from ripple
    # Phase noise ~ 20*log10(Kvco * Vripple / f_offset)
    kvco = raw_metrics.get("kvco", 200e6)
    if ripple_mv > 0:
        phase_noise_est = 20 * math.log10(max(1e-15, kvco * ripple_mv * 1e-3 / 1e6))
        phase_noise_est = min(-20, phase_noise_est)  # cap at -20 dBc minimum
    else:
        phase_noise_est = -120  # very good
    target = SPECS["phase_noise_dBc"]["target"]
    # Phase noise is negative, more negative = better
    scores["phase_noise"] = min(1.0, max(0.0, (phase_noise_est - (-20)) / (target - (-20))))

    # Reference spur (estimated from charge pump mismatch and loop filter attenuation)
    ref_spur_est = -40 - 20 * math.log10(max(1e-3, ripple_mv / 100))
    target = SPECS["ref_spur_dBc"]["target"]
    scores["ref_spur"] = min(1.0, max(0.0, ref_spur_est / target))

    # Power (estimated from charge pump current)
    icp = raw_metrics.get("icp", 500e-6)
    vdd = raw_metrics.get("vdd", 3.3)
    power_mw = icp * vdd * 1000 * 2  # rough: 2x for both CP legs
    target = SPECS["power_mw"]["target"]
    scores["power"] = min(1.0, target / power_mw) if power_mw > 0 else 0.0

    # Stability
    is_stable = raw_metrics.get("is_stable", raw_metrics.get("ctrl_settled", 0))
    scores["stability"] = float(is_stable)

    # Weighted sum
    weights = [
        SPECS["lock_time_us"]["weight"],
        SPECS["ctrl_ripple_mv"]["weight"],
        SPECS["phase_noise_dBc"]["weight"],
        SPECS["ref_spur_dBc"]["weight"],
        SPECS["power_mw"]["weight"],
        SPECS["stability"]["weight"],
    ]
    score_values = [
        scores["lock_time"],
        scores["ctrl_ripple"],
        scores["phase_noise"],
        scores["ref_spur"],
        scores["power"],
        scores["stability"],
    ]

    total = sum(w * s for w, s in zip(weights, score_values))
    return total, scores


def evaluate(timeout=300):
    """Full evaluation pipeline."""
    print("=" * 60)
    print("PLL CHALLENGE EVALUATION")
    print("=" * 60)

    # Run simulation
    print("\n[1/4] Running ngspice simulation...")
    success, runtime, stdout, stderr = run_simulation(timeout)

    if not success:
        print(f"  SIMULATION FAILED (runtime: {runtime:.1f}s)")
        if "not found" in stderr:
            print(f"  ERROR: {stderr}")
        elif "TIMEOUT" in stderr:
            print(f"  ERROR: {stderr}")
        else:
            # Print last 20 lines of stderr for debugging
            lines = stderr.strip().split("\n")
            for line in lines[-20:]:
                print(f"  {line}")
        return {
            "status": "crash",
            "score": 0.0,
            "runtime": runtime,
            "error": stderr[-500:] if stderr else "unknown",
        }

    print(f"  Simulation completed in {runtime:.1f}s")

    # Parse metrics
    print("\n[2/4] Extracting metrics from simulation output...")
    raw_metrics = parse_metrics(stdout, stderr)

    # Analyze waveforms
    print("\n[3/4] Analyzing waveforms...")
    waveform_metrics = analyze_waveforms()
    raw_metrics.update(waveform_metrics)

    for k, v in sorted(raw_metrics.items()):
        print(f"  {k}: {v}")

    # Compute score
    print("\n[4/4] Computing score...")
    total_score, sub_scores = compute_score(raw_metrics)

    print(f"\n{'='*60}")
    print(f"SUB-SCORES:")
    for name, s in sub_scores.items():
        bar = "#" * int(s * 20)
        print(f"  {name:20s}: {s:.4f}  [{bar:20s}]")
    print(f"\n  TOTAL SCORE: {total_score:.6f}")
    print(f"{'='*60}")

    # Build result record
    result = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "score": round(total_score, 6),
        "sub_scores": {k: round(v, 4) for k, v in sub_scores.items()},
        "raw_metrics": {k: v for k, v in raw_metrics.items()},
        "runtime": round(runtime, 1),
        "status": "ok" if total_score > 0 else "fail",
    }

    # Append to results log
    results_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), RESULTS_FILE)
    with open(results_path, "a") as f:
        f.write(json.dumps(result) + "\n")

    print(f"\nResults appended to {RESULTS_FILE}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=300, help="Simulation timeout in seconds")
    args = parser.parse_args()
    result = evaluate(args.timeout)
    sys.exit(0 if result["status"] == "ok" else 1)
