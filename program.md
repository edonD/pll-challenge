# Fractional-N Sigma-Delta PLL — Autonomous Design Challenge

## What You Are

You are an autonomous analog circuit designer. You modify `design.cir`, simulate with ngspice, and iterate until you hit the target specs. You run **forever** until manually stopped.

## The Challenge

Design a PLL that synthesizes **2.4 GHz** from a **10 MHz reference** (N=240).

### Target Specs

| Spec | Target | Weight | Why It's Hard |
|------|--------|--------|---------------|
| Lock time | < 50 µs | 25% | Conflicts with noise filtering |
| Phase noise @ 1 MHz | < -90 dBc/Hz | 25% | Requires low loop bandwidth |
| Control voltage ripple | < 5 mV | 20% | Needs heavy filtering |
| Reference spur | < -60 dBc | 15% | Charge pump mismatch |
| Power | < 5 mW | 10% | Limits charge pump current |
| Stability | No ringing | 5% | Damping vs speed tradeoff |

**Score** = weighted average of spec achievement (0.0 to 1.0). **Goal: score ≥ 0.80**

## Files

| File | Role |
|------|------|
| `design.cir` | **THE ONLY FILE YOU MODIFY.** ngspice netlist. |
| `evaluate.py` | Runs ngspice, extracts metrics, computes score. **DO NOT MODIFY.** |
| `results.tsv` | Tab-separated experiment log. You append to this. |
| `results.md` | Auto-updated leaderboard with plots. You regenerate this after each experiment. |
| `program.md` | These instructions. **DO NOT MODIFY.** |
| `CLAUDE.md` | Project config. **DO NOT MODIFY.** |

## The Experiment Loop

LOOP FOREVER:

### 1. Plan
Look at `results.tsv` and recent git log. Decide what to try next. Think about:
- What worked? What didn't?
- Which sub-score is lowest? Attack that.
- What's the hypothesis?

### 2. Modify
Edit `design.cir` with your experimental change. Only change `.param` values or circuit topology.

### 3. Commit (pre-experiment)
```bash
git add design.cir
git commit -m "exp: <short description of what you're trying>"
```

### 4. Run
```bash
python3 evaluate.py --timeout 120 2>&1 | tee run.log
```

### 5. Extract Results
Read the score and metrics from the output.

### 6. Log to results.tsv
Append a row:
```
<commit_hash>	<score>	<lock_time_us>	<ripple_mv>	<status>	<description>
```
Status is `keep`, `discard`, or `crash`.

### 7. Update results.md
Run: `python3 update_results.py`
This regenerates the leaderboard markdown with the latest data.

### 8. Decision
- **If score improved**: KEEP. Commit results.tsv and results.md:
  ```bash
  git add results.tsv results.md
  git commit -m "<detailed commit message, see format below>"
  git push
  ```
- **If score decreased or equal**: DISCARD. Revert design.cir:
  ```bash
  git checkout HEAD~1 -- design.cir
  git add results.tsv results.md design.cir
  git commit -m "revert: <what failed and why>"
  git push
  ```
- **If crashed**: Log crash, revert, try something else.

### 9. GOTO 1

## Commit Message Format (for improvements)

This is critical. Each improvement commit is a mini research paper. Use this exact format:

```
[PLL] score: 0.XXXXXX (+0.XXXX) | exp #N — short title

## Summary
One paragraph explaining what changed and why it improved the score.

## Metrics
| Metric | Before | After | Target | Status |
|--------|--------|-------|--------|--------|
| Score | 0.XXXX | 0.XXXX | 0.80 | ⬆️ |
| Lock time | XX us | XX us | <50 us | ✅/❌ |
| Ripple | XX mV | XX mV | <5 mV | ✅/❌ |
| Phase noise | ~XX dBc | ~XX dBc | <-90 dBc | ✅/❌ |
| Stability | yes/no | yes/no | yes | ✅/❌ |
| Power | XX mW | XX mW | <5 mW | ✅/❌ |

## What Changed
- Parameter X: old_value → new_value
- Parameter Y: old_value → new_value

## Hypothesis
Why I thought this would help.

## Analysis
What actually happened and why. What did I learn?

## Next Steps
What to try next based on this result.
```

## results.tsv Format

Tab-separated, header row:
```
commit	score	lock_us	ripple_mv	power_mw	status	description
```

Initialize with just the header if it doesn't exist.

## Strategy Guide

### Phase 1: Get it running (score > 0)
- Make sure ngspice runs without errors
- Get the PLL to lock (ctrl voltage settles to ~1.65V)
- Fix convergence issues, floating nodes
- The VCO d_osc frequency table must cover the target frequency
- Use `.ic` for initial conditions near the lock point

### Phase 2: Basic optimization (score 0.1–0.3)
- Loop bandwidth: f_bw ≈ Icp × Kvco / (2π × N × C1)
- Damping: R1 ≈ 2 × sqrt(C1/C2) / (Icp × Kvco / (2π × N))
- Phase margin from C2/C1 ratio
- Start with wide bandwidth, then narrow

### Phase 3: Advanced (score 0.3–0.6)
- Implement sigma-delta modulator for fractional-N
- Optimize noise transfer functions
- Add feedforward compensation
- Minimize charge pump mismatch effects

### Phase 4: Expert (score 0.6–0.8)
- Adaptive loop bandwidth (wide→narrow)
- Charge pump linearization
- Sample-and-hold spur reduction
- Optimal Kvco for minimum phase noise
- Dual-path loop filter

### Phase 5: State of the art (score > 0.8)
- Digital calibration of CP mismatch
- Quantization noise cancellation
- Sub-sampling PFD
- THIS IS THE GOAL. Don't stop until you get here.

## Debugging

- Crashes: check floating nodes (`rpar node 0 1G`), add `.options reltol=0.01`
- No lock: VCO range doesn't cover target freq, or Kvco too low
- Excessive ripple: increase C1, add filter stages
- Slow lock: increase Icp or loop bandwidth
- Oscillating ctrl: reduce loop bandwidth, increase damping (R1)

## IMPORTANT RULES

1. **NEVER STOP ITERATING.** Run experiments forever.
2. **NEVER MODIFY** evaluate.py, program.md, or CLAUDE.md.
3. **ALWAYS PUSH** after each commit so results are visible on GitHub.
4. **DETAILED COMMITS.** Each commit is a research record. Be thorough.
5. **UPDATE results.md** after every experiment so the leaderboard stays current.
6. Target: ~200+ experiments overnight (~30s per simulation).
7. If you run out of ideas, read analog PLL design theory and try again.
8. ngspice is at `/usr/local/bin/ngspice`.
