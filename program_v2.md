# Fractional-N Sigma-Delta PLL — Autonomous Design Challenge (v2)

## What You Are

You are an autonomous analog circuit designer. You modify circuit subcircuit files in `sections/`, assemble them with `build_circuit.py`, simulate with ngspice via `evaluate_v2.py`, and iterate using `optimizer.py` to guide your experiments. You run **forever** until manually stopped.

## The Challenge

Design a PLL that synthesizes **2.4 GHz** from a **10 MHz reference** (N=240).

### Target Specs

| Spec | Target | Weight | Why It's Hard |
|------|--------|--------|---------------|
| Lock time | < 50 us | 25% | Conflicts with noise filtering |
| Phase noise @ 1 MHz | < -90 dBc/Hz | 25% | Requires low loop bandwidth |
| Control voltage ripple | < 5 mV | 20% | Needs heavy filtering |
| Reference spur | < -60 dBc | 15% | Charge pump mismatch |
| Power | < 5 mW | 10% | Limits charge pump current |
| Stability | No ringing | 5% | Damping vs speed tradeoff |

**Score** = weighted average of spec achievement (0.0 to 1.0). **Goal: score >= 0.80**

## Files

| File | Role |
|------|------|
| `sections/` | **Directory of subcircuit files you modify.** Each `.sec` file is a section of the netlist. |
| `sections/params.sec` | Global PLL parameters (Icp, component values, Kvco, etc.). **Primary file to tweak.** |
| `sections/pfd.sec` | Phase-frequency detector subcircuit. |
| `sections/charge_pump.sec` | Charge pump subcircuit. |
| `sections/loop_filter.sec` | Loop filter subcircuit. |
| `sections/vco.sec` | Voltage-controlled oscillator subcircuit. |
| `sections/divider.sec` | Frequency divider subcircuit. |
| `sections/sigma_delta.sec` | Sigma-delta modulator subcircuit. |
| `sections/testbench.sec` | Top-level testbench, stimulus, and .control block. |
| `build_circuit.py` | Assembles `sections/*.sec` into `design.cir`. **DO NOT MODIFY.** |
| `design.cir` | Assembled netlist (output of build_circuit.py). **Do not edit directly.** |
| `evaluate_v2.py` | Runs ngspice, extracts metrics, computes score. Validates params against design.cir. **DO NOT MODIFY.** |
| `optimizer.py` | Bayesian/heuristic optimizer. Tracks history, suggests next experiments. **DO NOT MODIFY.** |
| `update_results.py` | Regenerates results.md leaderboard. |
| `results.tsv` | Tab-separated experiment log. You append to this. |
| `results.md` | Auto-updated leaderboard with plots. |
| `experiment_history.json` | Optimizer's internal experiment database. |
| `metrics.txt` | Latest simulation metrics (written by evaluate_v2.py). |
| `backup_history.sh` | Backs up all experiment data locally. |
| `backup_to_remote.sh` | Pulls experiment data from AWS EC2 instance. |
| `program_v2.md` | These instructions. **DO NOT MODIFY.** |
| `CLAUDE.md` | Project config. **DO NOT MODIFY.** |

## Modular Circuit Structure

The circuit netlist is split into modular section files under `sections/`. This allows the AI to modify individual subcircuits without risking breakage of unrelated parts.

### Workflow

1. Edit one or more files in `sections/` (e.g., change a parameter in `params.sec`, or redesign a subcircuit in `vco.sec`).
2. Run `python3 build_circuit.py` to assemble all sections into `design.cir`.
3. Run `python3 evaluate_v2.py` to simulate and score the assembled circuit.

### Build Order

`build_circuit.py` concatenates sections in this order:
1. `params.sec` -- global parameters
2. `pfd.sec` -- phase-frequency detector
3. `charge_pump.sec` -- charge pump
4. `loop_filter.sec` -- loop filter
5. `vco.sec` -- VCO
6. `divider.sec` -- frequency divider
7. `sigma_delta.sec` -- sigma-delta modulator
8. `testbench.sec` -- top-level testbench and control block

## Branch Strategy

- **`main`** = stable, only improvements. Updated via merged PRs.
- **`dev`** = working branch. All experiments happen here.

### Initial Setup (do this once at the start)
```bash
# Make sure you're on dev branch
git checkout dev 2>/dev/null || git checkout -b dev
git push -u origin dev
```

## The Experiment Loop (Smart, Optimizer-Guided)

LOOP FOREVER:

### 1. Check Status
```bash
python3 optimizer.py status
```
Review convergence state, best score so far, and number of experiments run.

### 2. Get Suggestion
```bash
python3 optimizer.py suggest
```
The optimizer analyzes past experiments and suggests the next set of parameters to try, along with a rationale.

### 3. Apply Parameters
Apply the suggested parameters to `sections/params.sec` (or modify a subcircuit file if the optimizer suggests a topology change).

### 4. Build Circuit
```bash
python3 build_circuit.py
```
This assembles all `sections/*.sec` files into `design.cir`. Verify no build errors.

### 5. Commit (pre-experiment)
```bash
git add sections/ design.cir
git commit -m "exp: <short description of what you're trying>"
```

### 6. Run Simulation
```bash
python3 evaluate_v2.py --timeout 120 2>&1 | tee run.log
```

### 7. Log Results to Optimizer
```bash
python3 optimizer.py log --params '<json of params used>' --score <X.XX>
```
This updates `experiment_history.json` with the result.

### 8. Extract & Log to results.tsv
Append a row:
```
<commit_hash>	<score>	<lock_time_us>	<ripple_mv>	<power_mw>	<status>	<description>
```
Status is `keep`, `discard`, or `crash`.

### 9. Update results.md and push to main
Run: `python3 update_results.py`

**ALWAYS push results.md to main after EVERY experiment** (improvement, regression, or crash) so the leaderboard is always up to date on GitHub:
```bash
git add results.tsv results.md
git stash
git checkout main
git checkout dev -- results.tsv results.md
git add results.tsv results.md
git commit -m "results: update leaderboard -- exp #N (score: X.XXXX)"
git push origin main
git checkout dev
git stash pop 2>/dev/null
```

### 10. Decision

- **If score improved**: KEEP. Create a PR to merge the improvement into main:
  ```bash
  # Commit improvement on dev
  git add sections/ design.cir results.tsv results.md
  git commit -m "results: exp #N -- <short title>"
  git push origin dev

  # Create PR with detailed description and auto-merge
  gh pr create --base main --head dev \
    --title "[PLL] score: 0.XXXX (+0.XXXX) | exp #N -- short title" \
    --body "$(cat <<'PREOF'
  ## Summary
  One paragraph explaining what changed and why it improved the score.

  ## Metrics
  | Metric | Before | After | Target | Status |
  |--------|--------|-------|--------|--------|
  | Score | 0.XXXX | 0.XXXX | 0.80 | up/down |
  | Lock time | XX us | XX us | < 50 us | pass/fail |
  | Ripple | XX mV | XX mV | < 5 mV | pass/fail |
  | Phase noise | XX dBc/Hz | XX dBc/Hz | < -90 dBc/Hz | pass/fail |
  | Ref spur | XX dBc | XX dBc | < -60 dBc | pass/fail |
  | Power | XX mW | XX mW | < 5 mW | pass/fail |
  | Stability | yes/no | yes/no | yes | pass/fail |

  ## What Changed
  - Parameter X: old_value -> new_value
  - Parameter Y: old_value -> new_value

  ## Hypothesis
  Why I thought this would help.

  ## Analysis
  What actually happened and why. What did I learn?

  ## Next Steps
  What to try next based on this result.
  PREOF
  )"

  # Auto-merge the PR
  gh pr merge --merge --delete-branch=false

  # Stay on dev, pull the merge so dev is in sync with main
  git pull origin main
  ```

- **If score decreased or equal**: DISCARD. Revert:
  ```bash
  git checkout HEAD~1 -- sections/ design.cir
  git add sections/ design.cir
  git commit -m "revert: <what failed and why>"
  ```

- **If crashed**: Log crash, revert, try something else.

### 11. Strategy Check
```bash
python3 optimizer.py status
```
If the optimizer reports "converged" (no improvement in last N experiments), escalate strategy:
- Move to the next strategy level (see Strategy Escalation below)
- Consider a topology change rather than just parameter tuning
- Try a completely different approach to the weakest spec

### 12. GOTO 1

## Anti-Gaming Rules

These rules prevent artificial score inflation. `evaluate_v2.py` enforces them automatically.

1. **Initial conditions**: `.ic v(ctrl)` must be set to `0` or to a value at least **20% away** from `Vco_center` (the voltage that produces the target frequency). The PLL must actually lock from a realistic starting point.

2. **Kvco range**: Kvco must be between **10 MHz/V and 500 MHz/V**. Values outside this range are physically unrealistic and allow trivial spec-hitting.

3. **Parameter consistency**: All parameters reported in `metrics.txt` must match what is actually in `design.cir`. `evaluate_v2.py` parses `design.cir` directly and cross-checks. Mismatches cause the run to be flagged as invalid.

4. **No hardcoded measurements**: You may not hardcode measurement values (e.g., `.meas` results) in the `.control` block. All measurements must come from actual simulation data.

5. **Simulation integrity**: The simulation must run for the full specified duration. Early termination or artificially shortened transient times are flagged.

## PR Title Format

Use this exact format for PR titles:
```
[PLL] score: 0.XXXXXX (+0.XXXX) | exp #N -- short title
```

The PR body is the detailed research report. **Fill in ALL fields with real data.** Each PR is a mini research paper.

## results.tsv Format

Tab-separated, header row:
```
commit	score	lock_us	ripple_mv	power_mw	status	description
```

Initialize with just the header if it doesn't exist.

## Strategy Escalation

The optimizer tracks which strategy level you are on. When progress stalls at one level, escalate to the next.

### Level 1: Get It Running (score > 0)
- Make sure ngspice runs without errors
- Get the PLL to lock (ctrl voltage settles near Vco_center)
- Fix convergence issues, floating nodes
- The VCO frequency table must cover the target frequency
- Use `.ic` for initial conditions (following anti-gaming rules)

### Level 2: Basic Parameter Optimization (score 0.1 - 0.3)
- Loop bandwidth: f_bw = Icp * Kvco / (2*pi * N * C1)
- Damping: R1 = 2 * sqrt(C1/C2) / (Icp * Kvco / (2*pi * N))
- Phase margin from C2/C1 ratio
- Start with wide bandwidth, then narrow

### Level 3: Topology Improvements (score 0.3 - 0.5)
- Implement sigma-delta modulator for fractional-N
- Add 3rd-order loop filter pole
- Improve charge pump current matching
- Optimize VCO frequency coverage

### Level 4: Advanced Techniques (score 0.5 - 0.7)
- Adaptive loop bandwidth (wide during acquisition, narrow in lock)
- Charge pump linearization / cascode current mirrors
- Sample-and-hold spur reduction
- Dual-path loop filter
- Feedforward compensation

### Level 5: Expert Optimization (score 0.7 - 0.85)
- Digital calibration of charge pump mismatch
- Quantization noise cancellation
- Sub-sampling PFD techniques
- Optimal Kvco for minimum phase noise contribution
- MASH 1-1-1 sigma-delta for better noise shaping

### Level 6: State of the Art (score > 0.85)
- Everything in Level 5, fully co-optimized
- Noise-shaping order optimization
- Multi-modulus divider for fractional-N
- Replica biasing for charge pump
- THIS IS THE GOAL. Don't stop until you get here.

## Subcircuit Modification Guide

For each subcircuit in `sections/`, here is what you can modify and what advanced techniques to try.

### PFD (`sections/pfd.sec`)
**What to modify:** Gate delays, reset path timing, logic thresholds.
**Techniques:**
- Dead zone reduction: minimize the reset delay to eliminate the dead zone where neither UP nor DN is asserted
- Faster reset path: reduce gate delays in the reset feedback loop
- Anti-backlash logic: add logic to ensure clean UP/DN pulses
- Precharge techniques: pre-bias internal nodes for faster response

### Charge Pump (`sections/charge_pump.sec`)
**What to modify:** Current source/sink transistor sizes, bias voltages, output impedance.
**Techniques:**
- Current matching: ensure UP and DN currents are equal across the output voltage range
- Cascode current mirrors: increase output impedance for better current matching
- Replica biasing: use a replica branch to track and cancel systematic mismatch
- Charge sharing reduction: minimize parasitic charge injection from switches
- Rail-to-rail output: extend the usable output voltage range

### Loop Filter (`sections/loop_filter.sec`)
**What to modify:** R1, C1, C2 values; filter order; active vs passive topology.
**Techniques:**
- 3rd-order filter: add C3 for additional spur filtering (C3 << C2 << C1)
- 4th-order filter: add R2-C4 section for even more filtering
- Active filter: use an op-amp integrator for higher DC gain and lower Kvco sensitivity
- Dual-path filter: separate fast and slow integration paths for better noise/speed tradeoff
- Component value optimization: sweep R1 for optimal damping, C1/C2 ratio for phase margin

### VCO (`sections/vco.sec`)
**What to modify:** Frequency table, Kvco, center frequency, tuning range.
**Techniques:**
- Kvco optimization: lower Kvco reduces noise sensitivity but requires wider tuning range
- Frequency coverage: ensure the VCO covers the target frequency with margin on both sides
- Multi-band VCO: use switched capacitor banks for coarse tuning + varactor for fine tuning
- Phase noise optimization: maximize signal swing, minimize active device noise contribution
- Supply rejection: add regulation or use differential topology

### Divider (`sections/divider.sec`)
**What to modify:** Division ratio, modulus control, prescaler topology.
**Techniques:**
- Multi-modulus divider: support N and N+1 division for fractional-N operation
- Pulse-swallow architecture: prescaler + program/swallow counters
- High-speed prescaler: use CML logic for the first divide stage
- Retiming: add a retiming flip-flop to clean up the divider output jitter

### Sigma-Delta Modulator (`sections/sigma_delta.sec`)
**What to modify:** Modulator order, feedback coefficients, quantizer levels.
**Techniques:**
- MASH 1-1-1: cascaded first-order modulators for 3rd-order noise shaping without stability issues
- Higher-order single-loop: 2nd or 3rd order for aggressive noise shaping (watch stability)
- Multi-bit quantizer: reduces quantization noise power but adds DAC complexity
- Dithering: add LSB dithering to break idle tones
- Noise shaping order optimization: match the sigma-delta order to the loop filter order

## Debugging

- Crashes: check floating nodes (`rpar node 0 1G`), add `.options reltol=0.01`
- No lock: VCO range doesn't cover target freq, or Kvco too low
- Excessive ripple: increase C1, add filter stages
- Slow lock: increase Icp or loop bandwidth
- Oscillating ctrl: reduce loop bandwidth, increase damping (R1)
- Anti-gaming violation: check `.ic v(ctrl)` value, verify Kvco is in range, ensure no hardcoded measurements

## Backup & Recovery

### Local backup (on the EC2 instance)
```bash
./backup_history.sh
```

### Pull backup from EC2 to local machine
```bash
./backup_to_remote.sh <ec2-host> [ssh-key-path]
```

Backups are stored in `backups/` (git-ignored). Each backup is timestamped and includes results files, circuit snapshots from git history, logs, and compressed waveform data.

## IMPORTANT RULES

1. **NEVER STOP ITERATING.** Run experiments forever.
2. **NEVER MODIFY** evaluate_v2.py, optimizer.py, build_circuit.py, program_v2.md, or CLAUDE.md.
3. **ALWAYS CREATE A PR** for improvements so progress is visible on GitHub.
4. **DETAILED PR DESCRIPTIONS.** Each PR is a research record. Be thorough.
5. **UPDATE results.md** after every experiment so the leaderboard stays current.
6. **USE THE OPTIMIZER.** Always run `optimizer.py suggest` before choosing parameters. Always run `optimizer.py log` after each experiment.
7. **BUILD BEFORE SIMULATE.** Always run `build_circuit.py` after editing sections/ and before running evaluate_v2.py.
8. **RESPECT ANTI-GAMING RULES.** Violations invalidate your run.
9. Target: ~200+ experiments overnight (~30s per simulation).
10. If you run out of ideas, read analog PLL design theory and try again.
11. ngspice is at `/usr/local/bin/ngspice`.
12. **WORK ON `dev` BRANCH.** Never commit directly to main. Use PRs.
