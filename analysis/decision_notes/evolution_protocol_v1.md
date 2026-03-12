# Evolution Protocol v1

## Goal

Build a **top-down batch self-evolution loop** for MathVista, where the system evolves
environment-to-environment (not just prompt-to-prompt).

A candidate environment may include:
- prompt patch changes
- tool-routing changes
- new tool creation
- answer extraction changes
- wrapper / controller changes

The key principle is:

> Use one batch to generate the next candidate, and the next unseen batch to validate it.

---

## Core online evolution structure

For round `k`:

- Current accepted environment: `v_k`
- Discovery batch: `T1_k`
- Validation batch: `T2_k`
- Candidate generated from `T1_k`: `v_{k+1}^cand`

Validation logic:
- Run `v_k` on `T2_k`
- Run `v_{k+1}^cand` on `T2_k`
- If candidate beats base by a required margin, accept
- Otherwise reject and keep `v_k`

---

## Batch splitting

Each round uses **two sequential 100-problem blocks**:

- `T1` = first 100 problems of the round window
- `T2` = next 100 problems of the round window

Each block is further split:

### T1 (candidate generation side)
- `T1-discovery` = 80
- `T1-probe` = 20

### T2 (future validation side)
- `T2-fastval` = 20
- `T2-fullval` = 80

---

## Meaning of each subset

### T1-discovery
Used to:
- run current accepted environment
- collect traces / failures
- produce candidate environment

### T1-probe
Used only as a **cheap internal screen**.
This subset must **not** be used for official acceptance.

### T2-fastval
Used for cheap future-side screening.
Only candidates that look promising here should move to full validation.

### T2-fullval
Used for official acceptance / rejection.

---

## Candidate is an environment delta

A candidate should not be restricted to "a new prompt".

A candidate environment may modify:

1. Prompt protocol
2. Tool selection policy
3. Tool invocation constraints
4. New tool creation
5. Wrapper/controller logic
6. Extraction / scoring logic

Therefore, validation is always:

> base environment vs candidate environment

not merely patch text vs patch text.

---

## Minimal round lifecycle

### Step 1: prepare
Run accepted base `v_k` on `T1-discovery`.

Artifacts:
- base results on `T1-discovery`
- traces
- diagnostic analysis inputs

### Step 2: generate candidate
Use `T1-discovery` results to produce a candidate environment `v_{k+1}^cand`.

This candidate may be represented by:
- a new repo copy
- a branch / workspace
- a manifest describing changed files and intended mechanism

### Step 3: cheap screening (optional but recommended)
Use `T1-probe` as a cheap internal screen to eliminate obviously poor candidates.

Important:
- `T1-probe` is not the official future validation set
- passing `T1-probe` does not imply acceptance

### Step 4: future fast validation
Run both base and candidate on `T2-fastval`.

If candidate does not beat base by the required fastval margin:
- reject early
- do not spend fullval cost

### Step 5: future full validation
Only fastval-passing candidates run on `T2-fullval`.

### Step 6: decision
If candidate beats base on `T2-fullval` by required margin:
- accept candidate
- next accepted version becomes `v_{k+1}`

Else:
- reject candidate
- keep `v_k`

---

## Minimal acceptance rule v1

### Fastval gate
Candidate proceeds to fullval only if:

- `acc(candidate, T2-fastval) - acc(base, T2-fastval) >= fastval_margin`

Recommended:
- `fastval_margin = 0.10` for a 20-question fastval (i.e. +2/20)

### Fullval accept
Candidate is accepted only if:

- `acc(candidate, T2-fullval) - acc(base, T2-fullval) >= fullval_margin`

Recommended:
- `fullval_margin = 0.025` for an 80-question fullval (i.e. +2/80)

---

## Important design principle

The system should evolve **top-down**:

1. First build a stable online batch evolution road
2. Then improve candidate generation quality
3. Then improve patch/tool families
4. Then improve acceptance sophistication

Do **not** start by over-optimizing single pid-level hooks before the batch-evolution road exists.

---

## Why this protocol fits the project

This protocol:
- avoids validating on the same problems used to create the candidate
- is simple enough to run now
- supports prompt, tool-routing, and new-tool evolution
- can later be extended with repeated evaluation, confidence-aware routing, and cached observations

---

## What is intentionally NOT in v1

This v1 protocol does not yet require:
- repeated evaluation everywhere
- multi-candidate tournaments
- sophisticated risk-aware rollback
- per-mechanism weighted scoring
- shadow replay of old batches for acceptance

These can be added later after the main road is running.

---

## Recommended artifacts per round

For each round, save:

- base results on `T1-discovery`
- candidate manifest
- base results on `T2-fastval`
- candidate results on `T2-fastval`
- base results on `T2-fullval` (if needed)
- candidate results on `T2-fullval` (if needed)
- decision json
- summary md

---

## Naming convention suggestion

Example round names:

- `round_001`
- `round_002`

Example environment version names:

- `v1`
- `v2`
- `v3`

Example results tree:

results/
  batch_evolve_v1/
    round_001/
      subsets/
      prepare/
      validate/
      decision.json
      summary.md

---

## Long-term path

After v1 works, the next upgrades should be:

1. better candidate manifests
2. tool-aware candidate generation
3. repeated evaluation only for near-threshold candidates
4. cached base results
5. family-aware routing
6. environment-level learning of tool creation policies

That is the path from:
- "patch experiments"
to
- "real self-evolving environments"
