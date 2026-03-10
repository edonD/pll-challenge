"""
PLL Challenge Evaluation Script v2 — Hardened against gaming.
DO NOT MODIFY THIS FILE.

Runs ngspice simulation, extracts metrics from DESIGN.CIR (never trusting
metrics.txt for parameter values), computes score with anti-gaming checks.

Key differences from v1:
  - All circuit parameters parsed directly from design.cir
  - Lock time penalised when initial conditions pre-bias near final value
  - Ripple scoring uses strict inequality (at-target gets half credit)
  - Phase noise uses REAL Kvco from design.cir
  - Reference spur computed via FFT of ctrl waveform when available
  - Power computed from parsed Icp/Vdd, not self-reported metrics.txt
  - Anti-gaming validation of design parameters

Usage: python evaluate_v2.py [--timeout 120] [--design design.cir]
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
    "lock_time_us":    {"target": 50,    "weight": 0.25, "direction": "lower",  "unit": "us"},
    "ctrl_ripple_mv":  {"target": 5,     "weight": 0.20, "direction": "lower",  "unit": "mV"},
    "phase_noise_dBc": {"target": -90,   "weight": 0.25, "direction": "lower",  "unit": "dBc/Hz@1MHz"},
    "ref_spur_dBc":    {"target": -60,   "weight": 0.15, "direction": "lower",  "unit": "dBc"},
    "power_mw":        {"target": 5,     "weight": 0.10, "direction": "lower",  "unit": "mW"},
    "stability":       {"target": 1.0,   "weight": 0.05, "direction": "higher", "unit": "bool"},
}

METRICS_FILE = "metrics.txt"
RESULTS_FILE = "results.jsonl"

# ---------------------------------------------------------------------------
# SPICE numeric suffix handling
# ---------------------------------------------------------------------------
_SPICE_SUFFIXES = {
    "t": 1e12,  "T": 1e12,
    "g": 1e9,   "G": 1e9,
    "meg": 1e6, "MEG": 1e6, "Meg": 1e6,
    "k": 1e3,   "K": 1e3,
    "m": 1e-3,  "M": 1e6,       # M is ambiguous; SPICE uses MEG for 1e6
    "u": 1e-6,  "U": 1e-6,
    "n": 1e-9,  "N": 1e-9,
    "p": 1e-12, "P": 1e-12,
    "f": 1e-15, "F": 1e-15,
}

# We need to be careful: "m" in SPICE means milli, "M" can also mean milli in
# some SPICE dialects, but "MEG" is mega.  We match the longest suffix first.


def _parse_spice_number(s):
    """
    Parse a SPICE numeric value like '1k', '100p', '200e6', '10e-3', '1.5G',
    '150meg', etc. Returns a float.
    """
    s = s.strip().rstrip("}")  # handle trailing brace from {value}
    s = s.lstrip("{")

    # Try pure float first (handles 200e6, 1.5e-3, plain numbers)
    try:
        return float(s)
    except ValueError:
        pass

    # Try suffix match (longest suffix first)
    for suffix in sorted(_SPICE_SUFFIXES, key=len, reverse=True):
        if s.lower().endswith(suffix.lower()):
            num_part = s[: len(s) - len(suffix)]
            try:
                return float(num_part) * _SPICE_SUFFIXES[suffix]
            except ValueError:
                continue

    raise ValueError(f"Cannot parse SPICE number: '{s}'")


# ---------------------------------------------------------------------------
# Design parameter parser
# ---------------------------------------------------------------------------
def parse_design_params(filepath):
    """
    Parse design.cir and extract all .param name = value lines.
    Also extracts VCO d_osc freq_array and cntl_array.
    Returns dict with parameter names -> float values, plus special keys
    'vco_freq_array' and 'vco_cntl_array' (lists of floats).
    """
    params = {}
    vco_freq_array = []
    vco_cntl_array = []
    ic_values = {}

    if not os.path.exists(filepath):
        print(f"  WARNING: Design file not found: {filepath}")
        return params

    with open(filepath, "r") as f:
        content = f.read()

    # ---- Parse .param lines ----
    # Handles:  .param Kvco = 200e6   or  .param R1 = 1k   etc.
    for m in re.finditer(
        r"\.param\s+(\w+)\s*=\s*([\d.eE+\-]+[a-zA-Z]*)", content, re.IGNORECASE
    ):
        name = m.group(1)
        raw_val = m.group(2)
        try:
            params[name] = _parse_spice_number(raw_val)
        except ValueError:
            print(f"  WARNING: Could not parse .param {name} = {raw_val}")

    # ---- Parse d_osc model for VCO lookup tables ----
    # Match cntl_array and freq_array across possibly multiple lines
    # First join continuation lines (lines starting with +)
    joined = re.sub(r"\n\+\s*", " ", content)

    cntl_m = re.search(r"cntl_array\s*=\s*\[([\d.eE+\-\s]+)\]", joined, re.IGNORECASE)
    freq_m = re.search(r"freq_array\s*=\s*\[([\d.eE+\-\s]+)\]", joined, re.IGNORECASE)

    if cntl_m:
        vco_cntl_array = [float(x) for x in cntl_m.group(1).split()]
    if freq_m:
        vco_freq_array = [float(x) for x in freq_m.group(1).split()]

    params["_vco_cntl_array"] = vco_cntl_array
    params["_vco_freq_array"] = vco_freq_array

    # ---- Parse .ic lines ----
    # .ic v(ctrl)={Vco_center} v(lf1)={Vco_center}
    for ic_m in re.finditer(
        r"\.ic\s+(.*)", content, re.IGNORECASE
    ):
        ic_line = ic_m.group(1)
        for vm in re.finditer(r"v\((\w+)\)\s*=\s*\{?([^}\s]+)\}?", ic_line, re.IGNORECASE):
            node_name = vm.group(1)
            val_expr = vm.group(2)
            # val_expr might be a param reference like Vco_center, or a number
            try:
                ic_values[node_name] = _parse_spice_number(val_expr)
            except ValueError:
                # It references a param name
                if val_expr in params:
                    ic_values[node_name] = params[val_expr]
                else:
                    ic_values[node_name] = val_expr  # store as string

    params["_ic_values"] = ic_values

    return params


# ---------------------------------------------------------------------------
# Anti-gaming validation
# ---------------------------------------------------------------------------
def validate_design(params):
    """
    Check that design parameters are physically reasonable.
    Returns (warnings: list[str], penalty_factor: float).
    penalty_factor is 0.0-1.0 where 1.0 = no penalty.
    """
    warnings = []
    penalty = 1.0

    kvco = params.get("Kvco", None)
    if kvco is not None:
        if kvco < 1e6:
            warnings.append(
                f"GAMING DETECTED: Kvco={kvco:.2e} Hz/V is unrealistically low "
                f"for a 2.4 GHz PLL (expected 1e6 to 1e9). Penalty applied."
            )
            penalty *= 0.3
        elif kvco > 1e9:
            warnings.append(
                f"WARNING: Kvco={kvco:.2e} Hz/V is very high. May indicate "
                f"instability or unrealistic design."
            )
            penalty *= 0.7
    else:
        warnings.append("WARNING: Kvco not found in design.cir")

    ndiv = params.get("Ndiv", None)
    fref = params.get("fref", None)
    if ndiv and fref:
        f_target = ndiv * fref
        if abs(f_target - 2.4e9) / 2.4e9 > 0.1:
            warnings.append(
                f"WARNING: Target frequency = Ndiv*fref = {f_target:.3e} Hz "
                f"deviates >10% from 2.4 GHz specification."
            )

    # Check VCO tuning range covers target frequency
    freq_arr = params.get("_vco_freq_array", [])
    if freq_arr:
        f_min = min(freq_arr)
        f_max = max(freq_arr)
        f_target_freq = 2.4e9
        if f_target_freq < f_min or f_target_freq > f_max:
            warnings.append(
                f"WARNING: VCO tuning range [{f_min:.3e}, {f_max:.3e}] Hz "
                f"does not cover target 2.4 GHz."
            )
            penalty *= 0.5

    # Check loop filter physical realizability
    r1 = params.get("R1", None)
    if r1 is not None:
        if r1 > 1e6:
            warnings.append(
                f"WARNING: R1={r1:.2e} ohm exceeds 1 Mohm — may be unrealizable."
            )
            penalty *= 0.8
        if r1 < 1:
            warnings.append(
                f"WARNING: R1={r1:.2e} ohm is unrealistically small."
            )
            penalty *= 0.5

    for cname in ["C1", "C2", "C3"]:
        cval = params.get(cname, None)
        if cval is not None:
            if cval < 0.1e-12:
                warnings.append(
                    f"WARNING: {cname}={cval:.2e} F is below 0.1 pF — may be unrealizable."
                )
                penalty *= 0.9

    icp = params.get("Icp", None)
    if icp is not None:
        if icp < 1e-6:
            warnings.append(
                f"WARNING: Icp={icp:.2e} A is very low (<1uA). May indicate gaming."
            )
            penalty *= 0.7
        if icp > 10e-3:
            warnings.append(
                f"WARNING: Icp={icp:.2e} A is very high (>10mA). Unusual design."
            )

    vdd = params.get("vdd", None)
    if vdd is not None:
        if vdd < 0.5 or vdd > 5.5:
            warnings.append(
                f"WARNING: vdd={vdd} V is outside typical range (0.5-5.5 V)."
            )

    icp_mismatch = params.get("Icp_mismatch", None)
    if icp_mismatch is not None:
        if icp_mismatch < 0:
            warnings.append(
                f"WARNING: Icp_mismatch={icp_mismatch} is negative — not physical."
            )
        if icp_mismatch > 0.5:
            warnings.append(
                f"WARNING: Icp_mismatch={icp_mismatch} is >50% — very high mismatch."
            )

    return warnings, max(0.0, min(1.0, penalty))


# ---------------------------------------------------------------------------
# ngspice discovery and simulation
# ---------------------------------------------------------------------------
def find_ngspice():
    """Find ngspice binary."""
    paths = [
        "ngspice",
        "/usr/local/bin/ngspice",
        "/usr/bin/ngspice",
        os.path.expanduser("~/ngspice/bin/ngspice"),
    ]
    for p in paths:
        try:
            r = subprocess.run(
                [p, "--version"], capture_output=True, timeout=5
            )
            if r.returncode == 0:
                return p
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return "ngspice"  # hope it's on PATH


def run_simulation(design_file, timeout=300):
    """Run ngspice on design_file and return (success, runtime, stdout, stderr)."""
    ngspice = find_ngspice()
    base_dir = os.path.dirname(os.path.abspath(design_file))
    t0 = time.time()
    try:
        result = subprocess.run(
            [ngspice, "-b", os.path.basename(design_file)],
            capture_output=True,
            timeout=timeout,
            cwd=base_dir,
        )
        runtime = time.time() - t0
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        return result.returncode == 0, runtime, stdout, stderr
    except subprocess.TimeoutExpired:
        return False, timeout, "", f"TIMEOUT after {timeout}s"
    except FileNotFoundError:
        return False, 0, "", f"ngspice not found at '{ngspice}'"


# ---------------------------------------------------------------------------
# Parse simulation output (metrics.txt + stdout)
# ---------------------------------------------------------------------------
def parse_metrics(stdout, stderr, base_dir):
    """
    Extract metrics from ngspice output and metrics.txt.
    NOTE: We only use metrics.txt for MEASURED quantities (lock_time,
    ctrl_final, ctrl_max, ctrl_min, ctrl_avg, ctrl_ripple).
    We NEVER trust it for design parameters (kvco, icp, vdd, etc.).
    """
    metrics = {}
    combined = stdout + "\n" + stderr

    # Parse from metrics.txt if it exists
    metrics_path = os.path.join(base_dir, METRICS_FILE)
    if os.path.exists(metrics_path):
        with open(metrics_path) as f:
            for line in f:
                m = re.match(r"(\w+):\s*([\d.eE+-]+)", line.strip())
                if m:
                    key = m.group(1)
                    try:
                        metrics[key] = float(m.group(2))
                    except ValueError:
                        pass

    # Also parse .meas results from stdout/stderr
    for line in combined.split("\n"):
        m = re.match(r"\s*(\w+)\s*=\s*([\d.eE+-]+)", line)
        if m:
            try:
                metrics[m.group(1)] = float(m.group(2))
            except ValueError:
                pass

    return metrics


# ---------------------------------------------------------------------------
# Waveform analysis (ctrl_waveform.txt)
# ---------------------------------------------------------------------------
def load_ctrl_waveform(base_dir):
    """Load ctrl_waveform.txt, return (times, values) as lists of float."""
    ctrl_file = os.path.join(base_dir, "ctrl_waveform.txt")
    times, values = [], []
    if not os.path.exists(ctrl_file):
        return times, values
    with open(ctrl_file) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                try:
                    times.append(float(parts[0]))
                    values.append(float(parts[1]))
                except ValueError:
                    continue
    return times, values


def analyze_waveforms(base_dir):
    """Analyze saved waveform data for additional metrics."""
    extra = {}
    times, values = load_ctrl_waveform(base_dir)

    if len(values) <= 100:
        return extra

    n = len(values)

    # ---- Stability: variance of last 10% ----
    tail = values[int(0.9 * n):]
    if tail:
        mean_tail = sum(tail) / len(tail)
        var_tail = sum((v - mean_tail) ** 2 for v in tail) / len(tail)
        std_tail = var_tail ** 0.5
        extra["ctrl_std_mv"] = std_tail * 1000
        extra["ctrl_settled"] = 1.0 if std_tail < 0.01 else 0.0

        # ---- Detect ringing: zero-crossings in last 20% ----
        tail20 = values[int(0.8 * n):]
        mean20 = sum(tail20) / len(tail20)
        crossings = sum(
            1 for i in range(1, len(tail20))
            if (tail20[i] - mean20) * (tail20[i - 1] - mean20) < 0
        )
        extra["zero_crossings"] = crossings
        extra["is_stable"] = 1.0 if crossings < 20 else 0.0

    # ---- Lock time from waveform ----
    if len(values) > 50:
        final_val = sum(values[-50:]) / min(50, len(values))
        band = abs(final_val) * 0.01 if final_val != 0 else 0.01
        lock_idx = None
        for i in range(len(values)):
            if abs(values[i] - final_val) < band:
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


# ---------------------------------------------------------------------------
# FFT-based reference spur measurement
# ---------------------------------------------------------------------------
def compute_reference_spur_fft(base_dir, fref):
    """
    Compute actual reference spur by performing FFT on the steady-state
    control voltage.  Returns the spur level in dBc at fref (and its
    first few harmonics), or None if not computable.
    """
    times, values = load_ctrl_waveform(base_dir)
    if len(values) < 200:
        return None

    n = len(values)
    # Use last 20% for steady-state analysis
    start_idx = int(0.8 * n)
    ss_times = times[start_idx:]
    ss_values = values[start_idx:]

    if len(ss_values) < 64:
        return None

    # Compute sample rate from time data
    dt_total = ss_times[-1] - ss_times[0]
    if dt_total <= 0:
        return None
    n_ss = len(ss_values)

    # Resample to uniform grid for FFT (simple linear interpolation)
    dt_uniform = dt_total / (n_ss - 1)
    fs = 1.0 / dt_uniform

    # Remove DC (mean)
    mean_v = sum(ss_values) / n_ss
    ac_values = [v - mean_v for v in ss_values]

    # Apply Hanning window
    windowed = []
    for i, v in enumerate(ac_values):
        w = 0.5 * (1 - math.cos(2 * math.pi * i / (n_ss - 1)))
        windowed.append(v * w)

    # Compute DFT magnitude (we don't have numpy, so do it manually for
    # the specific frequencies we care about, plus a small-N full DFT if
    # the data is short enough).  For efficiency, compute only at fref
    # and its harmonics.

    # Frequency resolution
    f_res = fs / n_ss

    best_spur_db = None
    dc_power = mean_v ** 2  # DC power for reference

    if dc_power < 1e-30:
        return None

    for harmonic in [1, 2, 3]:
        f_target = fref * harmonic
        if f_target >= fs / 2:
            break

        # Bin index closest to f_target
        k = round(f_target / f_res)
        if k <= 0 or k >= n_ss // 2:
            continue

        # DFT at bin k
        real_part = 0.0
        imag_part = 0.0
        for i_samp in range(n_ss):
            angle = -2.0 * math.pi * k * i_samp / n_ss
            real_part += windowed[i_samp] * math.cos(angle)
            imag_part += windowed[i_samp] * math.sin(angle)

        magnitude = math.sqrt(real_part ** 2 + imag_part ** 2) / n_ss
        # Convert to dBc relative to DC
        spur_db = 20 * math.log10(max(1e-20, magnitude / abs(mean_v)))

        if best_spur_db is None or spur_db > best_spur_db:
            best_spur_db = spur_db  # worst (highest) spur

    return best_spur_db


# ---------------------------------------------------------------------------
# Lock-time anti-gaming: detect initial condition bias
# ---------------------------------------------------------------------------
def check_ic_bias(params, raw_metrics):
    """
    Check whether .ic on ctrl is set near the final lock voltage.
    Returns (penalty_factor, explanation).
    penalty_factor = 1.0 means no penalty.
    """
    ic_values = params.get("_ic_values", {})
    ctrl_ic = ic_values.get("ctrl", None)

    if ctrl_ic is None or not isinstance(ctrl_ic, (int, float)):
        return 1.0, "No .ic for ctrl detected."

    ctrl_final = raw_metrics.get("ctrl_final", None)
    if ctrl_final is None:
        ctrl_final = raw_metrics.get("ctrl_avg", None)
    if ctrl_final is None:
        return 1.0, "Cannot determine ctrl_final to check IC bias."

    # How close is IC to final value?
    if abs(ctrl_final) < 1e-9:
        return 1.0, "ctrl_final ~ 0, cannot check IC bias."

    relative_error = abs(ctrl_ic - ctrl_final) / abs(ctrl_final)

    if relative_error < 0.05:
        # IC is within 5% of final value — this is pre-biased
        # Estimate realistic lock time from loop bandwidth
        kvco = params.get("Kvco", 200e6)
        icp = params.get("Icp", 500e-6)
        r1 = params.get("R1", 1e3)
        ndiv = params.get("Ndiv", 240)

        # Loop bandwidth estimate: f_bw ~ (Icp * Kvco * R1) / (2*pi*N)
        f_bw = (icp * kvco * r1) / (2 * math.pi * ndiv)
        if f_bw > 0:
            lock_time_realistic = 10.0 / f_bw
        else:
            lock_time_realistic = 100e-6

        explanation = (
            f"IC BIAS DETECTED: v(ctrl) IC={ctrl_ic:.4f} V is within "
            f"{relative_error * 100:.1f}% of ctrl_final={ctrl_final:.4f} V. "
            f"Estimated realistic lock time = {lock_time_realistic * 1e6:.1f} us "
            f"(based on f_bw={f_bw:.1f} Hz)."
        )
        return lock_time_realistic, explanation
    else:
        return 1.0, (
            f"IC check OK: v(ctrl) IC={ctrl_ic:.4f} V, "
            f"ctrl_final={ctrl_final:.4f} V, "
            f"error={relative_error * 100:.1f}%."
        )


# ---------------------------------------------------------------------------
# Score computation (hardened)
# ---------------------------------------------------------------------------
def compute_score(raw_metrics, design_params, base_dir):
    """
    Compute a 0-1 score from raw simulation metrics and parsed design params.
    Higher is better. 1.0 = all specs met perfectly.
    Returns (total, sub_scores, details) where details is a dict of extra info.
    """
    scores = {}
    details = {}

    # --- Extract real design parameters (NEVER from metrics.txt) ---
    kvco_real = design_params.get("Kvco", 200e6)
    icp_real = design_params.get("Icp", 500e-6)
    ndiv = design_params.get("Ndiv", 240)
    r1 = design_params.get("R1", 1e3)
    c1 = design_params.get("C1", 100e-12)
    c2 = design_params.get("C2", 10e-12)
    c3 = design_params.get("C3", 1e-12)
    vdd = design_params.get("vdd", 3.3)
    fref = design_params.get("fref", 10e6)
    icp_mismatch = design_params.get("Icp_mismatch", 0.02)
    vco_center = design_params.get("Vco_center", 1.65)

    details["kvco_real"] = kvco_real
    details["icp_real"] = icp_real
    details["vdd_real"] = vdd
    details["ndiv"] = ndiv
    details["r1"] = r1
    details["fref"] = fref

    # ========================
    # 1. LOCK TIME
    # ========================
    measured_lock_s = raw_metrics.get(
        "lock_time", raw_metrics.get("lock_time_from_waveform", 999)
    )
    lock_time_us_measured = measured_lock_s * 1e6
    if lock_time_us_measured <= 0:
        lock_time_us_measured = 999

    # Check for IC bias gaming
    ic_penalty_val, ic_explanation = check_ic_bias(design_params, raw_metrics)
    details["ic_check"] = ic_explanation

    if ic_penalty_val != 1.0:
        # ic_penalty_val is the estimated realistic lock time (seconds)
        lock_time_realistic_us = ic_penalty_val * 1e6
        # Use the WORSE of measured vs estimated
        lock_time_us_adjusted = max(lock_time_us_measured, lock_time_realistic_us)
        details["lock_time_measured_us"] = lock_time_us_measured
        details["lock_time_realistic_us"] = lock_time_realistic_us
        details["lock_time_used_us"] = lock_time_us_adjusted
    else:
        lock_time_us_adjusted = lock_time_us_measured
        details["lock_time_measured_us"] = lock_time_us_measured
        details["lock_time_used_us"] = lock_time_us_adjusted

    target = SPECS["lock_time_us"]["target"]
    scores["lock_time"] = (
        min(1.0, target / lock_time_us_adjusted) if lock_time_us_adjusted > 0 else 0.0
    )

    # ========================
    # 2. CONTROL VOLTAGE RIPPLE (strict scoring)
    # ========================
    ripple_mv = raw_metrics.get("ctrl_ripple", 999) * 1000
    if ripple_mv <= 0:
        ripple_mv = 999

    target = SPECS["ctrl_ripple_mv"]["target"]
    if ripple_mv < target:
        # Strictly below target: full credit
        scores["ctrl_ripple"] = min(1.0, target / ripple_mv)
    else:
        # At or above target: half credit that degrades further
        scores["ctrl_ripple"] = (target / ripple_mv) * 0.5

    details["ripple_mv_raw"] = ripple_mv
    details["ripple_scoring_note"] = (
        "strict: < target gets full credit, >= target gets half credit * (target/ripple)"
    )

    # ========================
    # 3. PHASE NOISE (using REAL Kvco)
    # ========================
    ripple_v = ripple_mv * 1e-3
    f_offset = 1e6  # 1 MHz offset

    if kvco_real > 0 and ripple_v > 0:
        # Phase noise estimate: 20*log10(Kvco * Vripple / f_offset)
        phase_noise_est = 20 * math.log10(
            max(1e-15, kvco_real * ripple_v / f_offset)
        )
        phase_noise_est = min(-20, phase_noise_est)  # cap at -20 dBc minimum
    else:
        phase_noise_est = -30  # poor default

    details["phase_noise_est_dBc"] = phase_noise_est
    details["phase_noise_formula"] = (
        f"20*log10(Kvco_real * Vripple / f_offset) = "
        f"20*log10({kvco_real:.2e} * {ripple_v:.2e} / {f_offset:.2e})"
    )

    target = SPECS["phase_noise_dBc"]["target"]
    # Phase noise is negative, more negative = better.
    # Score: linear mapping from -20 (worst=0) to target (best=1)
    scores["phase_noise"] = min(
        1.0, max(0.0, (phase_noise_est - (-20)) / (target - (-20)))
    )

    # ========================
    # 4. REFERENCE SPUR (FFT-based if possible)
    # ========================
    ref_spur_fft = compute_reference_spur_fft(base_dir, fref)

    if ref_spur_fft is not None:
        ref_spur_est = ref_spur_fft
        details["ref_spur_method"] = "FFT of ctrl waveform steady-state"
        details["ref_spur_fft_dBc"] = ref_spur_fft
    else:
        # Fallback: analytical estimate from ripple
        # Spur ~ Icp_mismatch * Kvco / (2*pi*N * f_offset) ... simplified to
        # using ripple as proxy
        ref_spur_est = -40 - 20 * math.log10(max(1e-3, ripple_mv / 100))
        details["ref_spur_method"] = "Analytical estimate (no ctrl_waveform.txt)"
        details["ref_spur_analytical_dBc"] = ref_spur_est

    details["ref_spur_used_dBc"] = ref_spur_est

    target = SPECS["ref_spur_dBc"]["target"]
    # ref_spur is negative dBc, more negative = better
    # Score: target is -60 dBc, anything <= target gets 1.0
    if ref_spur_est <= target:
        scores["ref_spur"] = 1.0
    elif ref_spur_est >= 0:
        scores["ref_spur"] = 0.0
    else:
        # Linear interpolation from 0 dBc (score=0) to target (score=1)
        scores["ref_spur"] = max(0.0, min(1.0, ref_spur_est / target))

    # ========================
    # 5. POWER (from parsed design params, NOT metrics.txt)
    # ========================
    # Charge pump power: Icp * Vdd * 2 (up and down legs)
    # Digital logic estimate: Vdd * 0.1mA
    power_mw = (icp_real * vdd * 2 + vdd * 0.1e-3) * 1000
    details["power_mw_computed"] = power_mw
    details["power_formula"] = (
        f"(Icp*vdd*2 + vdd*0.1mA)*1000 = "
        f"({icp_real:.2e}*{vdd}*2 + {vdd}*0.1e-3)*1000"
    )

    target = SPECS["power_mw"]["target"]
    scores["power"] = min(1.0, target / power_mw) if power_mw > 0 else 0.0

    # ========================
    # 6. STABILITY
    # ========================
    is_stable = raw_metrics.get("is_stable", raw_metrics.get("ctrl_settled", 0))
    scores["stability"] = float(is_stable)

    # ========================
    # Apply anti-gaming penalty
    # ========================
    validation_warnings, validation_penalty = validate_design(design_params)
    details["validation_warnings"] = validation_warnings
    details["validation_penalty"] = validation_penalty

    # ========================
    # Weighted sum
    # ========================
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

    raw_total = sum(w * s for w, s in zip(weights, score_values))
    penalized_total = raw_total * validation_penalty

    details["raw_total"] = raw_total
    details["penalized_total"] = penalized_total

    return penalized_total, scores, details


# ---------------------------------------------------------------------------
# Main evaluation pipeline
# ---------------------------------------------------------------------------
def evaluate(design_file, timeout=300):
    """Full evaluation pipeline."""
    base_dir = os.path.dirname(os.path.abspath(design_file))
    design_basename = os.path.basename(design_file)

    print("=" * 70)
    print("PLL CHALLENGE EVALUATION v2 (Hardened)")
    print("=" * 70)
    print(f"  Design file: {design_file}")
    print(f"  Timeout:     {timeout}s")

    # ---- Step 1: Parse design parameters ----
    print(f"\n[1/6] Parsing design parameters from {design_basename}...")
    design_params = parse_design_params(design_file)

    # Print parsed params (excluding internal keys)
    for k, v in sorted(design_params.items()):
        if k.startswith("_"):
            continue
        if isinstance(v, float):
            print(f"  {k:20s} = {v:.6g}")
        else:
            print(f"  {k:20s} = {v}")

    # VCO arrays
    cntl_arr = design_params.get("_vco_cntl_array", [])
    freq_arr = design_params.get("_vco_freq_array", [])
    if cntl_arr and freq_arr:
        print(f"  {'VCO cntl_array':20s} = {cntl_arr}")
        print(f"  {'VCO freq_array':20s} = {[f'{f:.3e}' for f in freq_arr]}")
    ic_vals = design_params.get("_ic_values", {})
    if ic_vals:
        print(f"  {'Initial conditions':20s} = {ic_vals}")

    # ---- Step 2: Validate design (anti-gaming) ----
    print(f"\n[2/6] Validating design parameters...")
    warnings, penalty = validate_design(design_params)
    if warnings:
        for w in warnings:
            print(f"  ** {w}")
        print(f"  Validation penalty factor: {penalty:.2f}")
    else:
        print("  All parameters within acceptable ranges.")

    # ---- Step 3: Run simulation ----
    print(f"\n[3/6] Running ngspice simulation on {design_basename}...")
    success, runtime, stdout, stderr = run_simulation(design_file, timeout)

    if not success:
        print(f"  SIMULATION FAILED (runtime: {runtime:.1f}s)")
        if "not found" in stderr:
            print(f"  ERROR: {stderr}")
        elif "TIMEOUT" in stderr:
            print(f"  ERROR: {stderr}")
        else:
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

    # ---- Step 4: Parse metrics ----
    print(f"\n[4/6] Extracting metrics from simulation output...")
    raw_metrics = parse_metrics(stdout, stderr, base_dir)

    # ---- Step 5: Analyze waveforms ----
    print(f"\n[5/6] Analyzing waveforms...")
    waveform_metrics = analyze_waveforms(base_dir)
    raw_metrics.update(waveform_metrics)

    print("  Raw measured metrics (from simulation):")
    for k, v in sorted(raw_metrics.items()):
        if isinstance(v, float):
            print(f"    {k:30s}: {v:.6g}")
        else:
            print(f"    {k:30s}: {v}")

    # Cross-check: warn if metrics.txt reports different params than design.cir
    metrics_kvco = raw_metrics.get("kvco", None)
    real_kvco = design_params.get("Kvco", None)
    if metrics_kvco is not None and real_kvco is not None:
        if abs(metrics_kvco - real_kvco) / max(abs(real_kvco), 1e-15) > 0.01:
            print(
                f"\n  ** GAMING ALERT: metrics.txt reports kvco={metrics_kvco:.2e} "
                f"but design.cir has Kvco={real_kvco:.2e}. Using design.cir value."
            )

    metrics_icp = raw_metrics.get("icp", None)
    real_icp = design_params.get("Icp", None)
    if metrics_icp is not None and real_icp is not None:
        if abs(metrics_icp - real_icp) / max(abs(real_icp), 1e-15) > 0.01:
            print(
                f"\n  ** GAMING ALERT: metrics.txt reports icp={metrics_icp:.2e} "
                f"but design.cir has Icp={real_icp:.2e}. Using design.cir value."
            )

    # ---- Step 6: Compute score ----
    print(f"\n[6/6] Computing score...")
    total_score, sub_scores, details = compute_score(
        raw_metrics, design_params, base_dir
    )

    # ---- Print detailed results ----
    print(f"\n{'=' * 70}")
    print("DETAILED SCORING BREAKDOWN")
    print(f"{'=' * 70}")

    # Lock time
    print(f"\n  Lock Time:")
    print(f"    Measured:    {details.get('lock_time_measured_us', 'N/A'):.2f} us"
          if isinstance(details.get('lock_time_measured_us'), (int, float))
          else f"    Measured:    {details.get('lock_time_measured_us', 'N/A')}")
    if 'lock_time_realistic_us' in details:
        print(f"    Realistic:   {details['lock_time_realistic_us']:.2f} us (from loop BW estimate)")
    print(f"    Used:        {details.get('lock_time_used_us', 'N/A'):.2f} us"
          if isinstance(details.get('lock_time_used_us'), (int, float))
          else f"    Used:        N/A")
    print(f"    IC check:    {details.get('ic_check', 'N/A')}")
    print(f"    Score:       {sub_scores['lock_time']:.4f}")

    # Ripple
    print(f"\n  Ctrl Ripple:")
    print(f"    Measured:    {details.get('ripple_mv_raw', 'N/A'):.3f} mV"
          if isinstance(details.get('ripple_mv_raw'), (int, float))
          else f"    Measured:    N/A")
    print(f"    Scoring:     {details.get('ripple_scoring_note', '')}")
    print(f"    Score:       {sub_scores['ctrl_ripple']:.4f}")

    # Phase noise
    print(f"\n  Phase Noise:")
    print(f"    Estimate:    {details.get('phase_noise_est_dBc', 'N/A'):.2f} dBc/Hz @1MHz"
          if isinstance(details.get('phase_noise_est_dBc'), (int, float))
          else f"    Estimate:    N/A")
    print(f"    Formula:     {details.get('phase_noise_formula', '')}")
    print(f"    Score:       {sub_scores['phase_noise']:.4f}")

    # Ref spur
    print(f"\n  Reference Spur:")
    print(f"    Method:      {details.get('ref_spur_method', 'N/A')}")
    print(f"    Estimate:    {details.get('ref_spur_used_dBc', 'N/A'):.2f} dBc"
          if isinstance(details.get('ref_spur_used_dBc'), (int, float))
          else f"    Estimate:    N/A")
    print(f"    Score:       {sub_scores['ref_spur']:.4f}")

    # Power
    print(f"\n  Power:")
    print(f"    Computed:    {details.get('power_mw_computed', 'N/A'):.4f} mW"
          if isinstance(details.get('power_mw_computed'), (int, float))
          else f"    Computed:    N/A")
    print(f"    Formula:     {details.get('power_formula', '')}")
    print(f"    Score:       {sub_scores['power']:.4f}")

    # Stability
    print(f"\n  Stability:")
    print(f"    Score:       {sub_scores['stability']:.4f}")

    # Validation
    if details.get("validation_warnings"):
        print(f"\n  Validation Warnings:")
        for w in details["validation_warnings"]:
            print(f"    ** {w}")

    # Summary
    print(f"\n{'=' * 70}")
    print("SUB-SCORES:")
    score_keys = ["lock_time", "ctrl_ripple", "phase_noise", "ref_spur", "power", "stability"]
    weight_keys = [
        "lock_time_us", "ctrl_ripple_mv", "phase_noise_dBc",
        "ref_spur_dBc", "power_mw", "stability",
    ]
    for sk, wk in zip(score_keys, weight_keys):
        s = sub_scores[sk]
        w = SPECS[wk]["weight"]
        bar = "#" * int(s * 20)
        contrib = w * s
        print(f"  {sk:20s}: {s:.4f}  (w={w:.2f}, contrib={contrib:.4f})  [{bar:20s}]")

    raw_t = details.get("raw_total", total_score)
    vp = details.get("validation_penalty", 1.0)
    print(f"\n  RAW TOTAL:       {raw_t:.6f}")
    if vp < 1.0:
        print(f"  VALIDATION PENALTY: x{vp:.2f}")
    print(f"  FINAL SCORE:     {total_score:.6f}")
    print(f"{'=' * 70}")

    # ---- Build result record ----
    result = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "evaluator": "v2",
        "design_file": design_basename,
        "score": round(total_score, 6),
        "score_raw": round(raw_t, 6),
        "validation_penalty": round(vp, 4),
        "sub_scores": {k: round(v, 4) for k, v in sub_scores.items()},
        "details": {
            k: (round(v, 6) if isinstance(v, float) else v)
            for k, v in details.items()
            if k != "validation_warnings"
        },
        "validation_warnings": details.get("validation_warnings", []),
        "raw_metrics": {
            k: v for k, v in raw_metrics.items()
            if k not in ("kvco", "icp", "vdd")  # exclude self-reported params
        },
        "design_params": {
            k: v for k, v in design_params.items()
            if not k.startswith("_")
        },
        "runtime": round(runtime, 1),
        "status": "ok" if total_score > 0 else "fail",
    }

    # Append to results log
    results_path = os.path.join(base_dir, RESULTS_FILE)
    with open(results_path, "a") as f:
        f.write(json.dumps(result) + "\n")

    print(f"\nResults appended to {RESULTS_FILE}")
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PLL Challenge Evaluation v2 (Hardened against gaming)"
    )
    parser.add_argument(
        "--timeout", type=int, default=300,
        help="Simulation timeout in seconds (default: 300)"
    )
    parser.add_argument(
        "--design", type=str, default="design.cir",
        help="Path to design .cir file (default: design.cir)"
    )
    args = parser.parse_args()

    # Resolve design file path
    design_path = args.design
    if not os.path.isabs(design_path):
        design_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), design_path
        )

    if not os.path.exists(design_path):
        print(f"ERROR: Design file not found: {design_path}")
        sys.exit(1)

    result = evaluate(design_path, args.timeout)
    sys.exit(0 if result["status"] == "ok" else 1)
