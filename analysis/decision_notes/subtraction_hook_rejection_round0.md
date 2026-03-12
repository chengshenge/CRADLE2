# Decision: reject current subtraction hook stack

## Scope
Current candidate:
- runner-level set_subtraction_single_pass gating
- prompt-level execution hook injection

## Evidence summary
Target-only repeated evaluation on pid 11 / 12 / 19 shows:

- pid 11 (GT=5)
  - hook on: 3, 6, 6
  - nohook: 7, 2, 6
  - result: no majority-correct improvement; current hook not acceptable

- pid 12
  - hook on: A, A, A
  - nohook: B, A, B
  - result: unstable under current evaluation regime; do not use single-run evidence

- pid 19 (GT=400)
  - hook on: 600, 600, 600
  - nohook: 600, 600, 600
  - result: independent residual, not caused by subtraction hook

## Decision
Reject current subtraction hook stack as accepted candidate.

## Keep / discard
Keep:
- hook gating / scope infrastructure
Discard:
- current subtraction hook content / workflow prompt

## Next branch
- pid 11 -> attribute / membership verification mechanism
- pid 19 -> measurement-target mechanism
