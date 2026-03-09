# Fractional-N Sigma-Delta PLL Autoresearch Challenge

## What you are

You are an autonomous analog design researcher. Your task is to design a high-performance fractional-N sigma-delta PLL frequency synthesizer by iteratively modifying `design.cir` and evaluating with ngspice.

## The Challenge

Design a PLL that synthesizes **2.40333... GHz** from a **10 MHz reference** (division ratio 240 + 1/3) meeting these specs:

| Specification | Target | Weight |
|---|---|---|
| Lock time | < 50 µs | 25% |
| Phase noise @ 1 MHz offset | < -90 dBc/Hz | 25% |
| Control voltage ripple | < 5 mV | 20% |
| Reference spur level | < -60 dBc | 15% |
| Power consumption | < 5 mW | 10% |
| Stability (no ringing) | Yes | 5% |

**Score** = weighted sum of individual spec achievement (0.0 to 1.0). Higher is better.

## Rules

### What you CAN modify
- `design.cir` — **THIS IS THE ONLY FILE YOU CHANGE**
- All `.param` values (component sizes, currents, voltages)
- Circuit topology within the PLL architecture (add/remove components)
- VCO implementation (B-source, XSPICE d_osc, etc.)
- Loop filter order and topology
- Sigma-delta modulator implementation
- Simulation parameters (.tran step, duration)
- Measurement commands

### What you CANNOT modify
- `evaluate.py` — the scoring function is fixed
- `program.md` — these instructions
- The target specs

### What you CAN add
- New `.subckt` definitions inside `design.cir`
- New `.model` definitions inside `design.cir`
- Helper stimulus files (`.stim`) if needed for XSPICE digital sources

## The Loop

Run this loop **forever** until manually stopped:

```
1. Read current design.cir and results.jsonl
2. Analyze what worked and what didn't
3. Form a hypothesis (e.g., "increasing R1 will reduce ripple")
4. Edit design.cir
5. Run: python evaluate.py --timeout 120
6. Read the output and score
7. If score IMPROVED:
   a. git add design.cir
   b. git commit with message containing:
      - Score (e.g., "score: 0.4521")
      - Delta from previous best (e.g., "+0.0312")
      - What changed and why
      - Key metrics snapshot
   c. Keep this as the new baseline
8. If score DECREASED or CRASHED:
   a. git checkout -- design.cir  (revert to last good version)
   b. Log what went wrong in your reasoning
   c. Try a different approach
9. GOTO 1
```

## Commit Message Format

```
[PLL] score: 0.XXXXXX (+0.XXXX) — short description

Metrics:
  lock_time:   XX.X us
  ripple:      XX.X mV
  phase_noise: -XX dBc/Hz
  stability:   yes/no

Change: <what you modified and why>
Hypothesis: <why you thought this would help>
Result: <what actually happened>
```

## Strategy Guide

### Phase 1: Get it running (score > 0)
- Make sure ngspice runs without errors
- Get the PLL to lock at all (ctrl voltage settles)
- Fix any simulation convergence issues
- Use `.ic` statements for initial conditions

### Phase 2: Basic optimization (score 0.1 - 0.3)
- Tune loop bandwidth: f_bw ≈ Icp * Kvco / (2π * N * C1)
- Set R1 for damping: R1 ≈ 2 * sqrt(C1 / C2) / (Icp * Kvco / (2π * N))
- Ensure phase margin > 45° (C2/C1 ratio)
- Reduce charge pump current for lower power

### Phase 3: Advanced optimization (score 0.3 - 0.6)
- Implement proper sigma-delta modulator (MASH 1-1-1)
- Optimize loop filter for noise shaping
- Add feedforward compensation
- Tune VCO gain vs control voltage range tradeoff
- Minimize charge pump mismatch effects

### Phase 4: Expert-level (score 0.6 - 0.8)
- Optimize noise transfer functions (type-II, 3rd order)
- Implement charge pump linearization
- Add sample-and-hold in loop filter for spur reduction
- Optimize transient response (critical damping)
- Consider adaptive loop bandwidth (wide for lock, narrow for steady state)

### Phase 5: State-of-the-art (score > 0.8)
- Implement digital calibration of charge pump mismatch
- Sub-sampling PFD technique for lower noise floor
- Implement quantization noise cancellation
- Dual-path loop filter topology
- Optimal Kvco for minimum phase noise

## Debugging Tips

- If ngspice crashes: check for floating nodes, add `rpar node 0 1G` to ground them
- If simulation doesn't converge: reduce timestep, add `.options reltol=0.01`
- If PLL doesn't lock: check VCO frequency range covers target, increase Kvco
- If excessive ripple: increase C1 or add more filtering stages
- If too slow to lock: increase charge pump current or loop bandwidth
- Use `echo` and `print` in .control block to debug values

## Important Notes

- ngspice must be in PATH on this system
- Each simulation should complete in < 2 minutes
- If a simulation hangs, it will be killed after the timeout
- Always test your changes before committing
- The evaluation script writes to results.jsonl — you can read this for history
- **NEVER STOP ITERATING.** If you run out of ideas, go back to fundamentals.
- Target: run ~200+ experiments overnight (assuming ~30s per simulation)
