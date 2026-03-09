# PLL Autoresearch Project

Read `program.md` for full instructions.

## Quick Summary
- You are an autonomous analog circuit designer
- Modify ONLY `design.cir` (ngspice netlist for a fractional-N sigma-delta PLL)
- Run `python3 evaluate.py` to score your design
- Git commit improvements with detailed metrics in commit message
- Revert failures with `git checkout -- design.cir`
- **NEVER STOP.** Run experiments forever until manually interrupted.
- ngspice binary is at `/usr/local/bin/ngspice`

## Commit Format
Every improvement commit MUST include:
```
[PLL] score: 0.XXXXXX (+0.XXXX) — short description

Metrics:
  lock_time:   XX.X us
  ripple:      XX.X mV
  phase_noise: ~XX dBc/Hz (estimated)
  stability:   yes/no
  ctrl_voltage: X.XX V

Change: what you modified
Hypothesis: why you thought it would help
Result: what actually happened
Experiment: N of total
```

## First Steps
1. Read `program.md` thoroughly
2. Read `design.cir` to understand current state
3. Read `evaluate.py` to understand scoring
4. Run `python3 evaluate.py` to get baseline
5. Start the optimization loop
