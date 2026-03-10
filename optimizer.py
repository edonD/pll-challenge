#!/usr/bin/env python3
"""
PLL Parameter Optimization Engine
==================================
Smart search strategy engine for CP-PLL parameter optimization.
Replaces brute-force guessing with structured exploration strategies.

Usage:
    python optimizer.py suggest [--strategy auto]
    python optimizer.py status
    python optimizer.py sensitivity
    python optimizer.py log --params '{"R1": 1000, ...}' --score 0.45 --description "increased R1"
    python optimizer.py history [--top 10]
    python optimizer.py analyze
    python optimizer.py apply --experiment <id>
"""

import argparse
import copy
import json
import math
import os
import random
import re
import sys
import time
from collections import defaultdict

# ---------------------------------------------------------------------------
# Optional imports -- graceful fallback if not installed
# ---------------------------------------------------------------------------
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

try:
    from scipy.optimize import minimize as scipy_minimize
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    from sklearn.ensemble import RandomForestRegressor
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "experiment_history.json")
DEFAULT_CIRCUIT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "design.cir")

# Parameter space for a 2.4 GHz CP-PLL
PARAM_SPACE = {
    "Icp": {
        "min": 10e-6, "max": 2e-3,
        "unit": "A", "scale": "log",
        "description": "Charge pump current",
    },
    "Kvco": {
        "min": 5e6, "max": 500e6,
        "unit": "Hz/V", "scale": "log",
        "description": "VCO gain",
    },
    "R1": {
        "min": 100, "max": 100e3,
        "unit": "Ohm", "scale": "log",
        "description": "Loop filter zero resistor",
    },
    "C1": {
        "min": 1e-12, "max": 100e-9,
        "unit": "F", "scale": "log",
        "description": "Loop filter zero capacitor",
    },
    "C2": {
        "min": 0.1e-12, "max": 10e-9,
        "unit": "F", "scale": "log",
        "description": "Loop filter integration capacitor",
    },
    "C3": {
        "min": 0.1e-12, "max": 1e-9,
        "unit": "F", "scale": "log",
        "description": "Loop filter HF filtering capacitor",
    },
    "Icp_mismatch": {
        "min": 0.001, "max": 0.1,
        "unit": "", "scale": "log",
        "description": "Charge pump up/down mismatch ratio",
    },
}

# Sub-score names and what parameters primarily influence them
SUBSCORE_PARAM_MAP = {
    "lock_time": {
        "primary": ["Icp", "Kvco", "R1", "C1"],
        "tip": "Increase Icp and loop bandwidth (Icp*Kvco/(2*pi*N*C1))",
    },
    "ctrl_ripple": {
        "primary": ["C1", "C2", "C3", "Icp_mismatch"],
        "tip": "Increase C1/C2, reduce Icp_mismatch, add filtering",
    },
    "phase_noise": {
        "primary": ["Kvco", "C1", "C2", "Icp"],
        "tip": "Reduce Kvco, increase filtering capacitors",
    },
    "ref_spur": {
        "primary": ["Icp_mismatch", "C1", "C2", "C3"],
        "tip": "Reduce Icp_mismatch, increase filter attenuation at fref",
    },
    "power": {
        "primary": ["Icp"],
        "tip": "Reduce Icp (but will hurt lock time)",
    },
    "stability": {
        "primary": ["R1", "C1", "C2"],
        "tip": "Increase damping: R1 ~ 2*sqrt(C1/C2)/(Icp*Kvco/(2*pi*N))",
    },
}

# Topology change suggestions
TOPOLOGY_SUGGESTIONS = [
    {
        "name": "sigma_delta_modulator",
        "description": "Add a sigma-delta modulator to the divider for fractional-N "
                       "operation. This spreads quantization noise out of band and "
                       "reduces reference spurs.",
        "changes": [
            "Add MASH 1-1-1 sigma-delta noise shaping to divider modulus control",
            "Increase loop filter order to suppress shaped noise",
        ],
    },
    {
        "name": "dual_path_loop_filter",
        "description": "Replace single-path loop filter with dual-path topology. "
                       "Separate integration and proportional paths allow independent "
                       "control of bandwidth and phase margin.",
        "changes": [
            "Split charge pump into integration and proportional paths",
            "Add separate filtering for each path",
            "Recombine at VCO input",
        ],
    },
    {
        "name": "sample_and_hold",
        "description": "Add a sample-and-hold block after the charge pump. This "
                       "eliminates charge pump current pulses from reaching the VCO, "
                       "dramatically reducing reference spurs.",
        "changes": [
            "Add S&H switch controlled by delayed reference edge",
            "Buffer VCO control voltage",
            "Re-tune loop filter for modified dynamics",
        ],
    },
    {
        "name": "adaptive_bandwidth",
        "description": "Implement gear-shifting: start with wide loop bandwidth for "
                       "fast acquisition, then switch to narrow bandwidth for low "
                       "noise in steady state.",
        "changes": [
            "Add lock detector circuit",
            "Switch Icp (or R1) between two values based on lock status",
            "Wide BW during acquisition, narrow BW when locked",
        ],
    },
]


# ===========================================================================
#  Utility helpers
# ===========================================================================

def _log_val(x):
    """Safe log10."""
    return math.log10(max(x, 1e-30))


def _exp_val(x):
    """10**x."""
    return 10.0 ** x


def _normalize(value, pmin, pmax, scale):
    """Map a raw parameter value to [0, 1]."""
    if scale == "log":
        return (_log_val(value) - _log_val(pmin)) / (_log_val(pmax) - _log_val(pmin))
    return (value - pmin) / (pmax - pmin)


def _denormalize(norm, pmin, pmax, scale):
    """Map [0, 1] back to raw parameter value."""
    if scale == "log":
        log_min = _log_val(pmin)
        log_max = _log_val(pmax)
        return _exp_val(log_min + norm * (log_max - log_min))
    return pmin + norm * (pmax - pmin)


def _clamp(value, pmin, pmax):
    """Clamp value to valid range."""
    return max(pmin, min(pmax, value))


def _format_eng(value, unit=""):
    """Format a number in engineering notation."""
    if value == 0:
        return f"0 {unit}"
    exp = int(math.floor(math.log10(abs(value)) / 3) * 3)
    prefixes = {
        -15: "f", -12: "p", -9: "n", -6: "u", -3: "m",
        0: "", 3: "k", 6: "M", 9: "G", 12: "T",
    }
    prefix = prefixes.get(exp, f"e{exp}")
    mantissa = value / (10 ** exp)
    if abs(mantissa - round(mantissa)) < 1e-6:
        return f"{int(round(mantissa))}{prefix}{unit}"
    return f"{mantissa:.3g}{prefix}{unit}"


def _format_param(name, value):
    """Human-readable parameter formatting."""
    info = PARAM_SPACE.get(name, {})
    unit = info.get("unit", "")
    return _format_eng(value, unit)


# ===========================================================================
#  Experiment History Database
# ===========================================================================

class ExperimentHistory:
    """JSON-backed experiment history database."""

    def __init__(self, path=None):
        self.path = path or HISTORY_FILE
        self.experiments = []
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self):
        """Load experiment history from JSON file."""
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    data = json.load(f)
                self.experiments = data if isinstance(data, list) else data.get("experiments", [])
            except (json.JSONDecodeError, IOError):
                self.experiments = []
        else:
            self.experiments = []

    def save(self):
        """Persist experiments to disk."""
        with open(self.path, "w") as f:
            json.dump(self.experiments, f, indent=2)

    # -- mutation -----------------------------------------------------------

    def add(self, parameters, score, sub_scores=None, status="ok",
            description="", hypothesis="", raw_metrics=None):
        """Add a new experiment record."""
        exp_id = len(self.experiments) + 1
        record = {
            "experiment_id": exp_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "parameters": parameters,
            "score": score,
            "sub_scores": sub_scores or {},
            "status": status,
            "description": description,
            "hypothesis": hypothesis,
            "raw_metrics": raw_metrics or {},
        }
        self.experiments.append(record)
        self.save()
        return record

    # -- queries ------------------------------------------------------------

    def best_experiment(self):
        """Return the experiment with the highest score."""
        valid = [e for e in self.experiments if e.get("status") != "crash"]
        if not valid:
            return None
        return max(valid, key=lambda e: e["score"])

    def top_n(self, n=10):
        """Return the top-N experiments by score."""
        valid = [e for e in self.experiments if e.get("status") != "crash"]
        return sorted(valid, key=lambda e: e["score"], reverse=True)[:n]

    def experiments_by_parameter(self, param_name):
        """Return experiments sorted by a specific parameter value."""
        valid = [e for e in self.experiments
                 if param_name in e.get("parameters", {})]
        return sorted(valid, key=lambda e: e["parameters"][param_name])

    def score_trend(self, window=10):
        """Return rolling average score over time."""
        scores = [e["score"] for e in self.experiments if e.get("status") != "crash"]
        if len(scores) < window:
            return scores
        trend = []
        for i in range(len(scores) - window + 1):
            trend.append(sum(scores[i:i + window]) / window)
        return trend

    def count(self):
        """Total number of experiments."""
        return len(self.experiments)

    def count_valid(self):
        """Number of non-crash experiments."""
        return sum(1 for e in self.experiments if e.get("status") != "crash")


# ===========================================================================
#  Latin Hypercube Sampling
# ===========================================================================

def _latin_hypercube_sample(n_samples, param_names=None, seed=None):
    """
    Generate n_samples parameter sets using Latin Hypercube Sampling.
    Returns a list of dicts mapping param_name -> value.
    """
    if seed is not None:
        random.seed(seed)

    if param_names is None:
        param_names = list(PARAM_SPACE.keys())

    n_params = len(param_names)
    # For each dimension, divide [0, 1] into n_samples equal strata,
    # then pick one random point per stratum and shuffle.
    samples = []
    for _ in range(n_params):
        perm = list(range(n_samples))
        random.shuffle(perm)
        col = []
        for i in range(n_samples):
            low = perm[i] / n_samples
            high = (perm[i] + 1) / n_samples
            col.append(random.uniform(low, high))
        samples.append(col)

    result = []
    for i in range(n_samples):
        params = {}
        for j, name in enumerate(param_names):
            info = PARAM_SPACE[name]
            params[name] = _denormalize(samples[j][i],
                                        info["min"], info["max"], info["scale"])
        result.append(params)
    return result


# ===========================================================================
#  Strategy: Gradient Descent (coordinate perturbation)
# ===========================================================================

def _suggest_gradient_descent(history):
    """
    Find the best experiment so far and perturb each parameter by +/-10%
    or +/-30% in log-space to estimate local gradients.  Return the most
    promising untested perturbation.
    """
    best = history.best_experiment()
    if best is None:
        return _latin_hypercube_sample(1)[0]

    base_params = best["parameters"]
    base_score = best["score"]

    # Gather all tested perturbations to avoid repeats
    tested = set()
    for exp in history.experiments:
        key = tuple(sorted(
            (k, round(_log_val(v) if PARAM_SPACE[k]["scale"] == "log" else v, 4))
            for k, v in exp.get("parameters", {}).items()
            if k in PARAM_SPACE
        ))
        tested.add(key)

    # Build candidate perturbations
    candidates = []
    perturbations = [0.70, 0.90, 1.10, 1.30]

    for pname in PARAM_SPACE:
        if pname not in base_params:
            continue
        info = PARAM_SPACE[pname]
        base_val = base_params[pname]

        for factor in perturbations:
            if info["scale"] == "log":
                new_val = base_val * factor
            else:
                new_val = base_val * factor
            new_val = _clamp(new_val, info["min"], info["max"])

            new_params = dict(base_params)
            new_params[pname] = new_val

            # Check if already tested (approximately)
            key = tuple(sorted(
                (k, round(_log_val(v) if PARAM_SPACE[k]["scale"] == "log" else v, 4))
                for k, v in new_params.items()
                if k in PARAM_SPACE
            ))
            if key not in tested:
                candidates.append({
                    "params": new_params,
                    "perturbed": pname,
                    "factor": factor,
                })

    if not candidates:
        # All single perturbations tried -- try multi-parameter perturbation
        new_params = dict(base_params)
        for pname in PARAM_SPACE:
            if pname not in new_params:
                continue
            info = PARAM_SPACE[pname]
            factor = random.choice([0.80, 0.90, 1.10, 1.20])
            new_params[pname] = _clamp(new_params[pname] * factor,
                                       info["min"], info["max"])
        return new_params

    # Score candidates heuristically using gradient estimates from history
    gradients = _estimate_gradients(history)
    best_cand = None
    best_expected = -1

    for cand in candidates:
        pname = cand["perturbed"]
        factor = cand["factor"]
        grad = gradients.get(pname, 0)

        direction = 1 if factor > 1 else -1
        # Expected improvement = gradient * step_size
        expected = grad * direction * abs(math.log(factor))
        if expected > best_expected or best_cand is None:
            best_expected = expected
            best_cand = cand

    return best_cand["params"]


def _estimate_gradients(history):
    """
    Estimate partial derivatives d(score)/d(log(param)) for each parameter
    using the experiment history.
    """
    gradients = {}
    for pname in PARAM_SPACE:
        info = PARAM_SPACE[pname]
        pairs = []
        for exp in history.experiments:
            if exp.get("status") == "crash":
                continue
            params = exp.get("parameters", {})
            if pname in params:
                if info["scale"] == "log":
                    x = _log_val(params[pname])
                else:
                    x = params[pname]
                pairs.append((x, exp["score"]))

        if len(pairs) < 2:
            gradients[pname] = 0
            continue

        # Simple linear regression: score = a*x + b
        pairs.sort(key=lambda p: p[0])
        n = len(pairs)
        sx = sum(p[0] for p in pairs)
        sy = sum(p[1] for p in pairs)
        sxy = sum(p[0] * p[1] for p in pairs)
        sxx = sum(p[0] ** 2 for p in pairs)

        denom = n * sxx - sx * sx
        if abs(denom) < 1e-30:
            gradients[pname] = 0
        else:
            gradients[pname] = (n * sxy - sx * sy) / denom

    return gradients


# ===========================================================================
#  Strategy: Bayesian-Simple (surrogate model)
# ===========================================================================

def _suggest_bayesian_simple(history):
    """
    Fit a surrogate model (Random Forest if sklearn available, else polynomial
    regression via least-squares) to the (params -> score) data.  Use expected
    improvement acquisition to pick the next point.
    """
    valid = [e for e in history.experiments if e.get("status") != "crash"
             and e.get("parameters")]
    if len(valid) < 10:
        return _suggest_gradient_descent(history)

    param_names = sorted(PARAM_SPACE.keys())

    # Build feature matrix X and target y
    X = []
    y = []
    for exp in valid:
        row = []
        for pname in param_names:
            info = PARAM_SPACE[pname]
            val = exp["parameters"].get(pname, (info["min"] + info["max"]) / 2)
            row.append(_normalize(val, info["min"], info["max"], info["scale"]))
        X.append(row)
        y.append(exp["score"])

    best_score = max(y)

    if HAS_SKLEARN and HAS_NUMPY:
        return _bayesian_sklearn(X, y, param_names, best_score)
    elif HAS_NUMPY:
        return _bayesian_numpy(X, y, param_names, best_score)
    else:
        return _bayesian_stdlib(X, y, param_names, best_score)


def _bayesian_sklearn(X, y, param_names, best_score):
    """Use sklearn RandomForest as surrogate."""
    X_arr = np.array(X)
    y_arr = np.array(y)

    rf = RandomForestRegressor(n_estimators=100, random_state=42)
    rf.fit(X_arr, y_arr)

    # Generate many random candidates and pick the one with best EI
    n_candidates = 5000
    candidates = np.random.rand(n_candidates, len(param_names))

    # Predict mean from the forest; estimate std from individual trees
    preds = np.array([tree.predict(candidates) for tree in rf.estimators_])
    mu = preds.mean(axis=0)
    sigma = preds.std(axis=0) + 1e-9

    # Expected Improvement
    z = (mu - best_score) / sigma
    # Approximate normal CDF/PDF using stdlib math
    ei = sigma * (z * _batch_norm_cdf(z) + _batch_norm_pdf(z))

    best_idx = np.argmax(ei)
    best_norm = candidates[best_idx]

    params = {}
    for j, pname in enumerate(param_names):
        info = PARAM_SPACE[pname]
        params[pname] = _denormalize(float(best_norm[j]),
                                     info["min"], info["max"], info["scale"])
    return params


def _batch_norm_cdf(z):
    """Vectorised normal CDF approximation (numpy)."""
    return 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2)))


def _batch_norm_pdf(z):
    """Vectorised normal PDF (numpy)."""
    return np.exp(-0.5 * z ** 2) / math.sqrt(2 * math.pi)


def _bayesian_numpy(X, y, param_names, best_score):
    """Use numpy polynomial regression as surrogate."""
    X_arr = np.array(X)
    y_arr = np.array(y)

    # Fit quadratic model: y ~ b0 + sum(bi*xi) + sum(bii*xi^2)
    n, d = X_arr.shape
    # Build design matrix with intercept, linear, and quadratic terms
    ones = np.ones((n, 1))
    design = np.hstack([ones, X_arr, X_arr ** 2])

    # Least squares
    try:
        coeffs, _, _, _ = np.linalg.lstsq(design, y_arr, rcond=None)
    except np.linalg.LinAlgError:
        return _suggest_gradient_descent(ExperimentHistory())

    # Generate candidates
    n_candidates = 5000
    candidates = np.random.rand(n_candidates, d)
    cand_design = np.hstack([np.ones((n_candidates, 1)),
                             candidates, candidates ** 2])
    pred = cand_design @ coeffs

    # Estimate uncertainty as distance from nearest training point
    dists = np.min(np.sum((candidates[:, None, :] - X_arr[None, :, :]) ** 2,
                          axis=2), axis=1)
    sigma = np.sqrt(dists) * 0.1 + 1e-9

    z = (pred - best_score) / sigma
    ei = sigma * (z * _batch_norm_cdf(z) + _batch_norm_pdf(z))
    best_idx = int(np.argmax(ei))
    best_norm = candidates[best_idx]

    params = {}
    for j, pname in enumerate(param_names):
        info = PARAM_SPACE[pname]
        params[pname] = _denormalize(float(best_norm[j]),
                                     info["min"], info["max"], info["scale"])
    return params


def _bayesian_stdlib(X, y, param_names, best_score):
    """
    Pure-stdlib fallback: simple interpolation with random exploration.
    Pick the top-5 experiments, create centroid, add random noise weighted
    by estimated gradients.
    """
    # Sort by score descending
    indexed = sorted(enumerate(y), key=lambda t: t[1], reverse=True)
    top_k = min(5, len(indexed))
    top_indices = [idx for idx, _ in indexed[:top_k]]

    # Compute centroid of top-k in normalized space
    d = len(param_names)
    centroid = [0.0] * d
    for idx in top_indices:
        for j in range(d):
            centroid[j] += X[idx][j]
    centroid = [c / top_k for c in centroid]

    # Add noise (exploration)
    noise_scale = max(0.05, 0.3 - 0.005 * len(y))  # decrease noise with more data
    result_norm = []
    for j in range(d):
        val = centroid[j] + random.gauss(0, noise_scale)
        val = max(0.0, min(1.0, val))
        result_norm.append(val)

    params = {}
    for j, pname in enumerate(param_names):
        info = PARAM_SPACE[pname]
        params[pname] = _denormalize(result_norm[j],
                                     info["min"], info["max"], info["scale"])
    return params


# ===========================================================================
#  Strategy: Focused Attack
# ===========================================================================

def _suggest_focused_attack(history):
    """
    Identify the weakest sub-score and suggest parameters that specifically
    target improving it.
    """
    best = history.best_experiment()
    if best is None:
        return _latin_hypercube_sample(1)[0]

    sub_scores = best.get("sub_scores", {})
    if not sub_scores:
        return _suggest_gradient_descent(history)

    # Find worst sub-score
    worst_name = min(sub_scores, key=lambda k: sub_scores[k])
    worst_val = sub_scores[worst_name]

    print(f"  [focused_attack] Weakest sub-score: {worst_name} = {worst_val:.4f}")

    params = dict(best["parameters"])
    mapping = SUBSCORE_PARAM_MAP.get(worst_name, {})
    primary_params = mapping.get("primary", [])
    tip = mapping.get("tip", "")

    if tip:
        print(f"  [focused_attack] Tip: {tip}")

    # Apply targeted adjustments
    if worst_name == "lock_time":
        # Speed up: increase Icp, increase loop BW
        if "Icp" in params:
            params["Icp"] = _clamp(params["Icp"] * 1.5,
                                   PARAM_SPACE["Icp"]["min"],
                                   PARAM_SPACE["Icp"]["max"])
        if "R1" in params:
            params["R1"] = _clamp(params["R1"] * 1.3,
                                  PARAM_SPACE["R1"]["min"],
                                  PARAM_SPACE["R1"]["max"])
        if "C1" in params:
            params["C1"] = _clamp(params["C1"] * 0.7,
                                  PARAM_SPACE["C1"]["min"],
                                  PARAM_SPACE["C1"]["max"])

    elif worst_name == "ctrl_ripple":
        # Reduce ripple: increase filter caps, reduce mismatch
        if "C1" in params:
            params["C1"] = _clamp(params["C1"] * 2.0,
                                  PARAM_SPACE["C1"]["min"],
                                  PARAM_SPACE["C1"]["max"])
        if "C2" in params:
            params["C2"] = _clamp(params["C2"] * 2.0,
                                  PARAM_SPACE["C2"]["min"],
                                  PARAM_SPACE["C2"]["max"])
        if "Icp_mismatch" in params:
            params["Icp_mismatch"] = _clamp(params["Icp_mismatch"] * 0.5,
                                            PARAM_SPACE["Icp_mismatch"]["min"],
                                            PARAM_SPACE["Icp_mismatch"]["max"])

    elif worst_name == "phase_noise":
        # Reduce phase noise: lower Kvco, increase filtering
        if "Kvco" in params:
            params["Kvco"] = _clamp(params["Kvco"] * 0.5,
                                    PARAM_SPACE["Kvco"]["min"],
                                    PARAM_SPACE["Kvco"]["max"])
        if "C1" in params:
            params["C1"] = _clamp(params["C1"] * 1.5,
                                  PARAM_SPACE["C1"]["min"],
                                  PARAM_SPACE["C1"]["max"])
        if "C2" in params:
            params["C2"] = _clamp(params["C2"] * 1.5,
                                  PARAM_SPACE["C2"]["min"],
                                  PARAM_SPACE["C2"]["max"])

    elif worst_name == "ref_spur":
        # Reduce spurs: lower mismatch, increase HF filtering
        if "Icp_mismatch" in params:
            params["Icp_mismatch"] = _clamp(params["Icp_mismatch"] * 0.3,
                                            PARAM_SPACE["Icp_mismatch"]["min"],
                                            PARAM_SPACE["Icp_mismatch"]["max"])
        if "C3" in params:
            params["C3"] = _clamp(params["C3"] * 3.0,
                                  PARAM_SPACE["C3"]["min"],
                                  PARAM_SPACE["C3"]["max"])
        if "C2" in params:
            params["C2"] = _clamp(params["C2"] * 1.5,
                                  PARAM_SPACE["C2"]["min"],
                                  PARAM_SPACE["C2"]["max"])

    elif worst_name == "power":
        # Reduce power: lower Icp
        if "Icp" in params:
            params["Icp"] = _clamp(params["Icp"] * 0.5,
                                   PARAM_SPACE["Icp"]["min"],
                                   PARAM_SPACE["Icp"]["max"])

    elif worst_name == "stability":
        # Improve stability: increase damping
        if "R1" in params and "C1" in params and "C2" in params:
            # Damping factor ~ R1 * sqrt(C2/C1) * (Icp*Kvco/(2*pi*N)) / 2
            # Increase R1, increase C2/C1 ratio
            params["R1"] = _clamp(params["R1"] * 2.0,
                                  PARAM_SPACE["R1"]["min"],
                                  PARAM_SPACE["R1"]["max"])
            params["C2"] = _clamp(params["C2"] * 0.5,
                                  PARAM_SPACE["C2"]["min"],
                                  PARAM_SPACE["C2"]["max"])

    # Add a small random perturbation to avoid getting stuck
    for pname in primary_params:
        if pname in params:
            info = PARAM_SPACE[pname]
            jitter = random.uniform(0.95, 1.05)
            params[pname] = _clamp(params[pname] * jitter,
                                   info["min"], info["max"])

    return params


# ===========================================================================
#  Strategy: Topology Change
# ===========================================================================

def _suggest_topology_change(history):
    """
    After convergence, suggest a circuit topology modification.
    Returns both parameter dict and a description of the topology change.
    """
    best = history.best_experiment()
    params = dict(best["parameters"]) if best else _latin_hypercube_sample(1)[0]

    # Pick a topology suggestion that hasn't been tried
    tried_topologies = set()
    for exp in history.experiments:
        desc = exp.get("description", "").lower()
        for ts in TOPOLOGY_SUGGESTIONS:
            if ts["name"].lower() in desc:
                tried_topologies.add(ts["name"])

    available = [ts for ts in TOPOLOGY_SUGGESTIONS
                 if ts["name"] not in tried_topologies]

    if not available:
        # All tried; cycle back to first with different params
        available = TOPOLOGY_SUGGESTIONS

    suggestion = available[0]

    print(f"\n  TOPOLOGY CHANGE SUGGESTION: {suggestion['name']}")
    print(f"  {suggestion['description']}")
    print(f"  Required changes:")
    for change in suggestion["changes"]:
        print(f"    - {change}")
    print()

    return params


# ===========================================================================
#  Main Strategy Router
# ===========================================================================

STRATEGY_ORDER = [
    "latin_hypercube",
    "gradient_descent",
    "bayesian_simple",
    "focused_attack",
    "topology_change",
]


def _auto_select_strategy(history):
    """Pick the best strategy based on how many experiments we have."""
    n = history.count_valid()
    best = history.best_experiment()
    convergence = detect_convergence(history)

    if n < 20:
        return "latin_hypercube"

    if convergence["converged"]:
        since = convergence["experiments_since_improvement"]
        if since >= 50:
            return "topology_change"
        if since >= 20:
            return "focused_attack"

    if n >= 50:
        return "bayesian_simple"

    return "gradient_descent"


def suggest_next(history, strategy="auto"):
    """
    Suggest the next set of parameters to try.

    Parameters
    ----------
    history : ExperimentHistory
    strategy : str
        One of: "auto", "latin_hypercube", "gradient_descent",
        "bayesian_simple", "focused_attack", "topology_change"

    Returns
    -------
    dict : parameter name -> value
    """
    if strategy == "auto":
        strategy = _auto_select_strategy(history)

    print(f"  Strategy: {strategy}")
    print(f"  Experiments so far: {history.count_valid()}")

    if strategy == "latin_hypercube":
        n_done = history.count_valid()
        # Generate full LHS plan, return the next untried sample
        samples = _latin_hypercube_sample(20, seed=42)
        idx = n_done % len(samples)
        params = samples[idx]

    elif strategy == "gradient_descent":
        params = _suggest_gradient_descent(history)

    elif strategy == "bayesian_simple":
        params = _suggest_bayesian_simple(history)

    elif strategy == "focused_attack":
        params = _suggest_focused_attack(history)

    elif strategy == "topology_change":
        params = _suggest_topology_change(history)

    else:
        print(f"  Unknown strategy '{strategy}', falling back to gradient_descent")
        params = _suggest_gradient_descent(history)

    # Ensure all params are clamped
    for pname in list(params.keys()):
        if pname in PARAM_SPACE:
            info = PARAM_SPACE[pname]
            params[pname] = _clamp(params[pname], info["min"], info["max"])

    return params


# ===========================================================================
#  Convergence Detection
# ===========================================================================

def detect_convergence(history):
    """
    Analyze experiment history to detect diminishing returns.

    Returns
    -------
    dict with keys: converged, best_score, experiments_since_improvement,
                    improvement_rate, suggested_action
    """
    valid = [e for e in history.experiments if e.get("status") != "crash"]
    if not valid:
        return {
            "converged": False,
            "best_score": 0.0,
            "experiments_since_improvement": 0,
            "improvement_rate": 0.0,
            "suggested_action": "continue",
        }

    scores = [e["score"] for e in valid]
    best_score = max(scores)

    # Find how many experiments since last improvement
    best_idx = 0
    running_best = -1
    for i, s in enumerate(scores):
        if s > running_best:
            running_best = s
            best_idx = i
    experiments_since_improvement = len(scores) - 1 - best_idx

    # Improvement rate over last 50 experiments
    window = min(50, len(scores))
    if window >= 2:
        recent = scores[-window:]
        # Linear regression: score vs experiment index
        n = len(recent)
        sx = sum(range(n))
        sy = sum(recent)
        sxy = sum(i * s for i, s in enumerate(recent))
        sxx = sum(i * i for i in range(n))
        denom = n * sxx - sx * sx
        improvement_rate = (n * sxy - sx * sy) / denom if abs(denom) > 1e-30 else 0.0
    else:
        improvement_rate = 0.0

    # Decision logic
    converged = False
    suggested_action = "continue"

    if experiments_since_improvement >= 100:
        converged = True
        if best_score >= 0.75:
            suggested_action = "stop"
        else:
            suggested_action = "change_topology"
    elif experiments_since_improvement >= 50:
        converged = True
        suggested_action = "change_topology"
    elif experiments_since_improvement >= 20:
        converged = True
        suggested_action = "escalate_strategy"

    return {
        "converged": converged,
        "best_score": best_score,
        "experiments_since_improvement": experiments_since_improvement,
        "improvement_rate": improvement_rate,
        "suggested_action": suggested_action,
    }


# ===========================================================================
#  Parameter Sensitivity Analysis
# ===========================================================================

def analyze_sensitivity(history):
    """
    Compute approximate partial derivatives of score w.r.t. each parameter.

    Returns
    -------
    dict : param_name -> { "gradient", "correlation", "importance", "direction" }
    """
    results = {}
    valid = [e for e in history.experiments
             if e.get("status") != "crash" and e.get("parameters")]
    if len(valid) < 3:
        print("  Need at least 3 valid experiments for sensitivity analysis.")
        return results

    for pname in PARAM_SPACE:
        info = PARAM_SPACE[pname]
        pairs = []
        for exp in valid:
            params = exp.get("parameters", {})
            if pname in params:
                if info["scale"] == "log":
                    x = _log_val(params[pname])
                else:
                    x = params[pname]
                pairs.append((x, exp["score"]))

        if len(pairs) < 3:
            results[pname] = {
                "gradient": 0.0, "correlation": 0.0,
                "importance": 0.0, "direction": "unknown",
            }
            continue

        # Linear regression
        n = len(pairs)
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        sx = sum(xs)
        sy = sum(ys)
        sxy = sum(x * y for x, y in pairs)
        sxx = sum(x * x for x in xs)
        syy = sum(y * y for y in ys)

        denom = n * sxx - sx * sx
        if abs(denom) < 1e-30:
            gradient = 0.0
        else:
            gradient = (n * sxy - sx * sy) / denom

        # Pearson correlation
        num_corr = n * sxy - sx * sy
        den_corr = math.sqrt(abs(denom) * abs(n * syy - sy * sy))
        correlation = num_corr / den_corr if den_corr > 1e-30 else 0.0

        results[pname] = {
            "gradient": gradient,
            "correlation": correlation,
            "importance": abs(correlation),
            "direction": "increase" if gradient > 0 else "decrease",
        }

    return results


# ===========================================================================
#  Circuit File Integration
# ===========================================================================

# Mapping from optimizer parameter names to .cir .param names
_CIR_PARAM_NAMES = {
    "Icp": "Icp",
    "Kvco": "Kvco",
    "R1": "R1",
    "C1": "C1",
    "C2": "C2",
    "C3": "C3",
    "Icp_mismatch": "Icp_mismatch",
}

# ngspice-friendly value formatting (no spaces, use engineering suffixes)
_SPICE_SUFFIXES = [
    (1e12, "T"), (1e9, "G"), (1e6, "Meg"), (1e3, "k"),
    (1, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"),
    (1e-12, "p"), (1e-15, "f"),
]


def _format_spice_value(value):
    """Format a float into ngspice-friendly notation (e.g. 100p, 1.5k)."""
    if value == 0:
        return "0"
    abs_val = abs(value)
    for threshold, suffix in _SPICE_SUFFIXES:
        if abs_val >= threshold * 0.999:
            scaled = value / threshold
            # Clean up trailing zeros
            if abs(scaled - round(scaled)) < 1e-6:
                return f"{int(round(scaled))}{suffix}"
            else:
                formatted = f"{scaled:.6g}"
                return f"{formatted}{suffix}"
    # Very small numbers: use scientific notation
    return f"{value:.6e}"


def apply_params_to_circuit(params, template_path=None, output_path=None):
    """
    Read a .cir template, replace .param values with the given parameters,
    and write the modified circuit.

    Parameters
    ----------
    params : dict
        Parameter name -> value  (optimizer names, e.g. "R1", "Icp")
    template_path : str or None
        Path to the template .cir file.  Defaults to design.cir.
    output_path : str or None
        Where to write the result.  Defaults to overwriting template_path.

    Returns
    -------
    str : path to the written file
    """
    if template_path is None:
        template_path = DEFAULT_CIRCUIT
    if output_path is None:
        output_path = template_path

    with open(template_path, "r") as f:
        lines = f.readlines()

    modified_lines = []
    replaced = set()

    for line in lines:
        new_line = line
        for opt_name, cir_name in _CIR_PARAM_NAMES.items():
            if opt_name not in params:
                continue
            # Match lines like:  .param R1 = 1k   or   .param Icp = 500e-6
            pattern = re.compile(
                r"^(\.param\s+" + re.escape(cir_name) + r"\s*=\s*)(\S+)(.*)",
                re.IGNORECASE,
            )
            m = pattern.match(line)
            if m:
                new_val = _format_spice_value(params[opt_name])
                new_line = f"{m.group(1)}{new_val}{m.group(3)}\n"
                replaced.add(opt_name)
                break

        # Special handling for Kvco: also update the VCO d_osc freq_array
        # if Kvco was changed.
        if "Kvco" in params and "freq_array" in line:
            kvco = params["Kvco"]
            vco_center = params.get("Vco_center", 1.65)
            f_target = 2.4e9
            cntl_points = [0.5, 1.0, vco_center, 2.0, 2.5]
            freq_points = [f_target + kvco * (v - vco_center) for v in cntl_points]
            freq_str = " ".join(f"{f:.4e}" for f in freq_points)
            new_line = re.sub(
                r"freq_array=\[.*?\]",
                f"freq_array=[{freq_str}]",
                new_line,
            )

        modified_lines.append(new_line)

    with open(output_path, "w") as f:
        f.writelines(modified_lines)

    if replaced:
        print(f"  Updated {len(replaced)} parameters in {output_path}")
    else:
        print(f"  WARNING: No parameters were replaced in {output_path}")

    return output_path


# ===========================================================================
#  CLI Interface
# ===========================================================================

def cmd_suggest(args):
    """Suggest next experiment."""
    history = ExperimentHistory()
    strategy = args.strategy if hasattr(args, "strategy") else "auto"

    print("=" * 60)
    print("SUGGEST NEXT EXPERIMENT")
    print("=" * 60)

    params = suggest_next(history, strategy=strategy)
    convergence = detect_convergence(history)

    print(f"\n  Suggested parameters:")
    print(f"  {'Parameter':<16} {'Value':<16} {'Range'}")
    print(f"  {'-'*16} {'-'*16} {'-'*30}")
    for pname in sorted(PARAM_SPACE.keys()):
        if pname in params:
            info = PARAM_SPACE[pname]
            formatted = _format_param(pname, params[pname])
            range_str = f"[{_format_eng(info['min'])}, {_format_eng(info['max'])}]"
            print(f"  {pname:<16} {formatted:<16} {range_str}")

    if convergence["converged"]:
        print(f"\n  NOTE: Search appears converged.")
        print(f"  Suggested action: {convergence['suggested_action']}")

    # Print JSON for programmatic consumption
    print(f"\n  JSON: {json.dumps(params)}")

    # Offer to apply to circuit
    if hasattr(args, "apply") and args.apply:
        apply_params_to_circuit(params)
        print(f"\n  Applied to {DEFAULT_CIRCUIT}")

    return params


def cmd_status(args):
    """Show convergence status."""
    history = ExperimentHistory()
    convergence = detect_convergence(history)

    print("=" * 60)
    print("OPTIMIZATION STATUS")
    print("=" * 60)

    n_total = history.count()
    n_valid = history.count_valid()
    best = history.best_experiment()

    print(f"\n  Total experiments:     {n_total}")
    print(f"  Valid experiments:     {n_valid}")
    print(f"  Crashed experiments:   {n_total - n_valid}")

    if best:
        print(f"\n  Best score:            {best['score']:.6f}")
        print(f"  Best experiment ID:    {best['experiment_id']}")
        print(f"  Best timestamp:        {best['timestamp']}")

        if best.get("sub_scores"):
            print(f"\n  Sub-scores:")
            for name, s in sorted(best["sub_scores"].items()):
                bar = "#" * int(s * 30)
                print(f"    {name:<20} {s:.4f}  [{bar:<30}]")

    print(f"\n  Converged:             {convergence['converged']}")
    print(f"  Since last improvement: {convergence['experiments_since_improvement']} experiments")
    print(f"  Improvement rate:      {convergence['improvement_rate']:.6f} / experiment")
    print(f"  Suggested action:      {convergence['suggested_action']}")

    # Score trend
    trend = history.score_trend(window=5)
    if trend:
        print(f"\n  Score trend (window=5):")
        max_trend = max(trend) if trend else 1
        for i, t in enumerate(trend[-10:]):
            bar_len = int(t / max(max_trend, 0.001) * 40)
            bar = "#" * bar_len
            print(f"    [{i:3d}] {t:.4f}  {bar}")

    # Auto strategy recommendation
    strategy = _auto_select_strategy(history)
    print(f"\n  Recommended strategy:  {strategy}")


def cmd_sensitivity(args):
    """Show parameter sensitivity analysis."""
    history = ExperimentHistory()

    print("=" * 60)
    print("PARAMETER SENSITIVITY ANALYSIS")
    print("=" * 60)

    results = analyze_sensitivity(history)
    if not results:
        print("\n  Insufficient data for sensitivity analysis.")
        return

    # Sort by importance
    sorted_params = sorted(results.items(), key=lambda kv: kv[1]["importance"],
                           reverse=True)

    print(f"\n  {'Parameter':<16} {'Importance':<12} {'Correlation':<14} "
          f"{'Direction':<12} {'Gradient'}")
    print(f"  {'-'*16} {'-'*12} {'-'*14} {'-'*12} {'-'*12}")

    for pname, info in sorted_params:
        imp_bar = "#" * int(info["importance"] * 20)
        print(f"  {pname:<16} {info['importance']:.4f}       "
              f"{info['correlation']:+.4f}        "
              f"{info['direction']:<12} {info['gradient']:+.6f}")
        print(f"  {'':16} [{imp_bar:<20}]")

    print(f"\n  Interpretation:")
    print(f"  - Higher importance = stronger effect on score")
    print(f"  - Direction = whether increasing the parameter helps or hurts")
    print(f"  - Correlation near +/-1 = clear linear relationship")

    # Provide actionable suggestions
    if sorted_params:
        most_important = sorted_params[0]
        pname = most_important[0]
        direction = most_important[1]["direction"]
        print(f"\n  Suggestion: Focus on '{pname}' ({direction} to improve score)")


def cmd_log(args):
    """Log an experiment result."""
    history = ExperimentHistory()

    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON for --params: {e}")
        sys.exit(1)

    sub_scores = {}
    if hasattr(args, "sub_scores") and args.sub_scores:
        try:
            sub_scores = json.loads(args.sub_scores)
        except json.JSONDecodeError:
            pass

    record = history.add(
        parameters=params,
        score=args.score,
        sub_scores=sub_scores,
        status=args.status if hasattr(args, "status") else "ok",
        description=args.description if hasattr(args, "description") else "",
        hypothesis=args.hypothesis if hasattr(args, "hypothesis") else "",
    )

    print(f"Logged experiment #{record['experiment_id']} (score: {args.score:.6f})")
    print(f"  History now has {history.count()} experiments.")


def cmd_history(args):
    """Show experiment history summary."""
    history = ExperimentHistory()
    top_n = args.top if hasattr(args, "top") else 10

    print("=" * 60)
    print("EXPERIMENT HISTORY")
    print("=" * 60)

    total = history.count()
    if total == 0:
        print("\n  No experiments recorded yet.")
        print(f"  Run: python optimizer.py suggest")
        return

    print(f"\n  Total experiments: {total}")
    print(f"  Valid experiments: {history.count_valid()}")

    best = history.best_experiment()
    if best:
        print(f"  Best score: {best['score']:.6f} (experiment #{best['experiment_id']})")

    # Top N
    top = history.top_n(top_n)
    if top:
        print(f"\n  Top {len(top)} experiments:")
        print(f"  {'#':<5} {'Score':<10} {'Timestamp':<20} {'Description'}")
        print(f"  {'-'*5} {'-'*10} {'-'*20} {'-'*30}")
        for exp in top:
            desc = exp.get("description", "")[:40]
            print(f"  {exp['experiment_id']:<5} {exp['score']:<10.6f} "
                  f"{exp['timestamp']:<20} {desc}")

    # Score timeline (last 20)
    valid = [e for e in history.experiments if e.get("status") != "crash"]
    if len(valid) > 1:
        print(f"\n  Score timeline (last 20 experiments):")
        recent = valid[-20:]
        max_score = max(e["score"] for e in recent)
        for exp in recent:
            bar_len = int(exp["score"] / max(max_score, 0.001) * 40)
            bar = "#" * bar_len
            print(f"    #{exp['experiment_id']:<4} {exp['score']:.4f}  {bar}")


def cmd_analyze(args):
    """Show parameter-score analysis (ASCII heatmaps)."""
    history = ExperimentHistory()

    print("=" * 60)
    print("PARAMETER-SCORE ANALYSIS")
    print("=" * 60)

    valid = [e for e in history.experiments
             if e.get("status") != "crash" and e.get("parameters")]
    if len(valid) < 5:
        print("\n  Need at least 5 valid experiments for analysis.")
        return

    # For each parameter, show score vs parameter value (ASCII scatter)
    for pname in sorted(PARAM_SPACE.keys()):
        info = PARAM_SPACE[pname]
        pairs = []
        for exp in valid:
            if pname in exp.get("parameters", {}):
                val = exp["parameters"][pname]
                pairs.append((val, exp["score"]))

        if len(pairs) < 3:
            continue

        print(f"\n  {pname} ({info['unit']}) vs Score")
        print(f"  {'-' * 50}")

        # Bin into 10 buckets
        if info["scale"] == "log":
            log_vals = [_log_val(p[0]) for p in pairs]
            lo, hi = min(log_vals), max(log_vals)
        else:
            lo = min(p[0] for p in pairs)
            hi = max(p[0] for p in pairs)

        n_bins = 10
        if abs(hi - lo) < 1e-30:
            print(f"    All values identical: {_format_eng(pairs[0][0], info['unit'])}")
            continue

        bin_width = (hi - lo) / n_bins
        bins = defaultdict(list)
        for val, score in pairs:
            if info["scale"] == "log":
                x = _log_val(val)
            else:
                x = val
            b = min(int((x - lo) / bin_width), n_bins - 1)
            bins[b].append(score)

        for b in range(n_bins):
            if info["scale"] == "log":
                bval = _exp_val(lo + (b + 0.5) * bin_width)
            else:
                bval = lo + (b + 0.5) * bin_width
            label = _format_eng(bval, info["unit"])
            scores_in_bin = bins.get(b, [])
            if scores_in_bin:
                avg = sum(scores_in_bin) / len(scores_in_bin)
                count = len(scores_in_bin)
                bar = "#" * int(avg * 30)
                print(f"    {label:>14}  ({count:2d}) {avg:.4f} {bar}")
            else:
                print(f"    {label:>14}  (  )  --")

    # Sub-score breakdown of the best experiment
    best = history.best_experiment()
    if best and best.get("sub_scores"):
        print(f"\n  Best experiment sub-score breakdown:")
        for name, s in sorted(best["sub_scores"].items()):
            bar = "#" * int(s * 30)
            status = "OK" if s >= 0.8 else "WEAK" if s >= 0.5 else "POOR"
            print(f"    {name:<20} {s:.4f}  [{bar:<30}]  {status}")


def cmd_apply(args):
    """Apply parameters from an experiment to the circuit file."""
    history = ExperimentHistory()

    if hasattr(args, "experiment") and args.experiment:
        exp_id = args.experiment
        matching = [e for e in history.experiments if e["experiment_id"] == exp_id]
        if not matching:
            print(f"ERROR: No experiment with ID {exp_id}")
            sys.exit(1)
        params = matching[0]["parameters"]
        print(f"Applying parameters from experiment #{exp_id}")
    elif hasattr(args, "params") and args.params:
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON: {e}")
            sys.exit(1)
        print("Applying provided parameters")
    else:
        print("ERROR: Provide --experiment <id> or --params '{...}'")
        sys.exit(1)

    template = args.template if hasattr(args, "template") and args.template else None
    output = args.output if hasattr(args, "output") and args.output else None
    apply_params_to_circuit(params, template_path=template, output_path=output)
    print("Done.")


# ---------------------------------------------------------------------------
#  Main entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="PLL Parameter Optimization Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python optimizer.py suggest                     # Suggest next experiment (auto strategy)
  python optimizer.py suggest --strategy bayesian_simple
  python optimizer.py suggest --apply             # Suggest and apply to design.cir
  python optimizer.py status                      # Show convergence status
  python optimizer.py sensitivity                 # Show parameter sensitivity
  python optimizer.py log --params '{"R1":1000}' --score 0.45 --description "test"
  python optimizer.py history --top 10            # Show top 10 experiments
  python optimizer.py analyze                     # Parameter-score analysis
  python optimizer.py apply --experiment 5        # Apply experiment #5 to design.cir
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # suggest
    sp_suggest = subparsers.add_parser("suggest", help="Suggest next experiment")
    sp_suggest.add_argument("--strategy", default="auto",
                            choices=["auto"] + STRATEGY_ORDER,
                            help="Optimization strategy")
    sp_suggest.add_argument("--apply", action="store_true",
                            help="Apply suggested params to design.cir")

    # status
    subparsers.add_parser("status", help="Show convergence status")

    # sensitivity
    subparsers.add_parser("sensitivity", help="Show parameter sensitivity")

    # log
    sp_log = subparsers.add_parser("log", help="Log an experiment result")
    sp_log.add_argument("--params", required=True,
                        help="JSON dict of parameter values")
    sp_log.add_argument("--score", type=float, required=True,
                        help="Overall score (0.0-1.0)")
    sp_log.add_argument("--sub-scores", dest="sub_scores", default="",
                        help="JSON dict of sub-scores")
    sp_log.add_argument("--status", default="ok",
                        choices=["ok", "crash", "fail"],
                        help="Experiment status")
    sp_log.add_argument("--description", default="",
                        help="Short description of the experiment")
    sp_log.add_argument("--hypothesis", default="",
                        help="What you expected to happen")

    # history
    sp_history = subparsers.add_parser("history", help="Show history summary")
    sp_history.add_argument("--top", type=int, default=10,
                            help="Number of top experiments to show")

    # analyze
    subparsers.add_parser("analyze", help="Parameter-score analysis")

    # apply
    sp_apply = subparsers.add_parser("apply",
                                     help="Apply params to circuit file")
    sp_apply.add_argument("--experiment", type=int,
                          help="Experiment ID to apply")
    sp_apply.add_argument("--params",
                          help="JSON dict of parameter values")
    sp_apply.add_argument("--template",
                          help="Template .cir file (default: design.cir)")
    sp_apply.add_argument("--output",
                          help="Output .cir file (default: overwrite template)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    commands = {
        "suggest": cmd_suggest,
        "status": cmd_status,
        "sensitivity": cmd_sensitivity,
        "log": cmd_log,
        "history": cmd_history,
        "analyze": cmd_analyze,
        "apply": cmd_apply,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
