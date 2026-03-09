# PLL Autoresearch Project

Read `program.md` for full instructions. This is your bible.

## Quick Start
1. Read `program.md` completely
2. Read `design.cir` — understand current circuit
3. Read `evaluate.py` — understand how scoring works (DO NOT MODIFY)
4. Set up branches: `git checkout dev 2>/dev/null || git checkout -b dev`
5. Run `python3 evaluate.py` for baseline
6. Start the experiment loop (see program.md)

## Key Commands
```bash
# Run experiment
python3 evaluate.py --timeout 120 2>&1 | tee run.log

# Update leaderboard
python3 update_results.py

# Create PR for improvement (on dev branch)
gh pr create --base main --head dev \
  --title "[PLL] score: X.XX (+X.XX) | exp #N — title" \
  --body "<detailed metrics report>"
gh pr merge --merge --delete-branch=false
git pull origin main

# Revert failed experiment (on dev branch)
git checkout HEAD~1 -- design.cir
```

## Rules
- ONLY modify `design.cir`
- WORK ON `dev` branch — never commit directly to main
- CREATE PRs for improvements — this is how progress is tracked on GitHub
- ALWAYS update results.md via `python3 update_results.py`
- ALWAYS use the detailed PR description format from program.md
- NEVER stop iterating
- ngspice is at `/usr/local/bin/ngspice`
- Target score: ≥ 0.80
