# PLL Autoresearch Project

Read `program.md` for full instructions. This is your bible.

## Quick Start
1. Read `program.md` completely
2. Read `design.cir` — understand current circuit
3. Read `evaluate.py` — understand how scoring works (DO NOT MODIFY)
4. Run `python3 evaluate.py` for baseline
5. Start the experiment loop (see program.md)

## Key Commands
```bash
# Run experiment
python3 evaluate.py --timeout 120 2>&1 | tee run.log

# Update leaderboard
python3 update_results.py

# Commit improvement
git add design.cir results.tsv results.md
git commit -m "<detailed message per program.md format>"
git push

# Revert failed experiment
git checkout HEAD~1 -- design.cir
```

## Rules
- ONLY modify `design.cir`
- ALWAYS push after commits
- ALWAYS update results.md via `python3 update_results.py`
- ALWAYS use the detailed commit message format from program.md
- NEVER stop iterating
- ngspice is at `/usr/local/bin/ngspice`
- Target score: ≥ 0.80
