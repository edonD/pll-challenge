# PLL Autoresearch Project (v2)

Read `program_v2.md` for full instructions. This is your bible.

## Quick Start
1. Read `program_v2.md` completely
2. Read files in `sections/` -- understand current subcircuits
3. Read `evaluate_v2.py` -- understand how scoring works (DO NOT MODIFY)
4. Read `optimizer.py` -- understand how experiment suggestions work (DO NOT MODIFY)
5. Set up branches: `git checkout dev 2>/dev/null || git checkout -b dev`
6. Run `python3 build_circuit.py && python3 evaluate_v2.py` for baseline
7. Start the experiment loop (see program_v2.md)

## Key Commands
```bash
# Build circuit from sections
python3 build_circuit.py

# Run experiment
python3 evaluate_v2.py --timeout 120 2>&1 | tee run.log

# Check optimizer status
python3 optimizer.py status

# Get next experiment suggestion
python3 optimizer.py suggest

# Log experiment result
python3 optimizer.py log --params '{"Icp": 100e-6, "C1": 10e-12}' --score 0.45

# Update leaderboard
python3 update_results.py

# Back up experiment history (on EC2)
./backup_history.sh

# Pull backup from EC2 to local
./backup_to_remote.sh <ec2-host> [ssh-key]

# Create PR for improvement (on dev branch)
gh pr create --base main --head dev \
  --title "[PLL] score: X.XX (+X.XX) | exp #N -- title" \
  --body "<detailed metrics report>"
gh pr merge --merge --delete-branch=false
git pull origin main

# Revert failed experiment (on dev branch)
git checkout HEAD~1 -- sections/ design.cir
```

## Rules
- Modify files in `sections/` -- never edit design.cir directly
- Run `build_circuit.py` after every edit to sections/ before simulating
- WORK ON `dev` branch -- never commit directly to main
- CREATE PRs for improvements -- this is how progress is tracked on GitHub
- ALWAYS update results.md via `python3 update_results.py`
- ALWAYS use the detailed PR description format from program_v2.md
- ALWAYS use `optimizer.py suggest` before choosing parameters
- ALWAYS use `optimizer.py log` after each experiment
- NEVER stop iterating
- ngspice is at `/usr/local/bin/ngspice`
- Target score: >= 0.80

## Anti-Gaming
`evaluate_v2.py` parses `design.cir` directly and validates parameters. It enforces:
- `.ic v(ctrl)` must be 0 or at least 20% away from Vco_center
- Kvco must be between 10 MHz/V and 500 MHz/V
- Parameters in metrics.txt must match design.cir (cross-checked automatically)
- No hardcoded measurement values in the .control block
Violations invalidate the experiment run.

## GitHub Repo
https://github.com/edonD/pll-challenge
