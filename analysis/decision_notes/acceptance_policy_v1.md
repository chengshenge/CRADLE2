# Acceptance Policy v1

## Goal
Reduce false accepts / false rejects caused by single-run randomness.

## Stage A: single-pass screen
For each new candidate:
- run 1 target-only evaluation first
- reject immediately if:
  - target does not improve, or
  - protected targets clearly regress

## Stage B: adaptive replicate
Only if candidate looks promising in Stage A:
- run 2 additional target-only evaluations
- accept to next stage only if:
  - target wins by majority (>= 2/3), and
  - protected targets do not show majority regression

## Stage C: larger confirmation
Only Stage-B-passing candidates may run on:
- discovery slice
- held-out val

## Notes
- Do not use single-run evidence for unstable targets.
- Prefer target-only repeated evaluation over full repeated discovery evaluation.
