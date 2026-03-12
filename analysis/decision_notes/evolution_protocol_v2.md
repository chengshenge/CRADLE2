# Evolution Protocol v2

## Goal

Upgrade the current online batch evolution road from:

- manual candidate selection
- automatic evaluation

to:

- automatic failure analysis
- automatic candidate policy selection
- automatic candidate environment proposal
- automatic evaluation

The core idea is:

> Candidate is an environment-level object, not just a prompt patch.

A candidate may change:

- prompt protocol
- tool routing
- answer extraction
- new tool creation
- wrapper/controller logic

---

## v2 stack

### Layer 1: Solver
Current Visual Sketchpad environment that actually solves problems.

### Layer 2: Failure Analysis
Reads `T1-discovery` outputs and produces a structured residual inventory.

### Layer 3: Candidate Policy
Chooses what class of candidate should be tried next:
- prompt candidate
- routing candidate
- tool candidate
- extraction candidate
- no-op candidate

### Layer 4: Candidate Builder
Builds the candidate manifest and the concrete environment delta.

### Layer 5: Evolution Controller
Runs:
- discovery
- failure analysis
- candidate policy
- candidate build
- fastval/fullval
- accept/reject

---

## Batch protocol (inherits v1)

For round `k`:

- current accepted environment = `v_k`
- discovery side = `T1_k`
- future validation side = `T2_k`

Each side is split:

### T1
- `T1-discovery`
- `T1-probe`

### T2
- `T2-fastval`
- `T2-fullval`

v2 still keeps the main rule:

> Candidate is generated from `T1`.
> Candidate is validated on unseen `T2`.

---

## Failure taxonomy v2

Initial taxonomy:

1. `comparison_relation`
2. `subtraction_membership`
3. `measurement_target`
4. `counting_visibility`
5. `tool_failure`
6. `extraction_failure`
7. `unknown`

This taxonomy is intentionally small in v2.1.

---

## Candidate policy mapping v2.1

### comparison_relation
Preferred candidate:
- `prompt_protocol_candidate`

### subtraction_membership
Preferred candidate:
- `prompt_protocol_candidate`
- specifically membership-verification style protocols

### measurement_target
Preferred candidate:
- `tool_routing_candidate`
- next stage may become `new_tool_candidate`

### counting_visibility
Preferred candidate:
- `prompt_protocol_candidate`

### tool_failure
Preferred candidate:
- `routing_candidate`

### extraction_failure
Preferred candidate:
- `extraction_candidate`

### unknown
Preferred candidate:
- `no_op_candidate`
- or human review if repeated

---

## v2.1 scope

In v2.1 we will implement only:

1. `analyze_failures_v2.py`
2. `propose_candidate_policy_v2.py`

This means:
- failure analysis becomes automatic
- candidate family selection becomes automatic

But:
- actual candidate environment build may still be partially manual for a short time

That is acceptable for v2.1.

---

## Expected outputs of analyze_failures_v2

Input:
- `T1-discovery` output json

Output:
- per-problem failure summary
- incorrect family counts
- dominant failure family
- target residual pids
- round-level summary

---

## Expected outputs of propose_candidate_policy_v2

Input:
- failure summary json

Output:
- candidate family
- candidate type
- rationale
- target pids
- expected builder action
- expected risks
- expected validation targets

---

## Acceptance remains unchanged in v2.1

Candidate acceptance still uses the same online batch rule:

- fastval first
- fullval second
- accept only if candidate beats base by required margin

v2.1 changes *how a candidate is chosen*, not *how a candidate is accepted*.

---

## Why v2 matters

Without v2, the system is:

- human chooses candidate
- system only evaluates candidate

With v2, the system becomes:

- system observes failures
- system chooses candidate family
- system moves toward automated environment evolution

This is the bridge from:

- manual patching + automatic evaluation

to:

- automatic candidate generation + automatic evaluation

---

## v2.1 non-goals

Not included yet:

- automatic code-level tool generation
- multi-candidate tournament search
- repeated evaluation everywhere
- learned policy over candidate families
- automatic rollback over many past versions

Those come later.

---

## v2.2 direction

After v2.1 works, next steps should be:

1. `build_candidate_env_v2.py`
2. prompt candidate auto-generation
3. routing candidate auto-generation
4. later: tool candidate auto-generation

---

## Practical principle

Do not optimize single pid-level hooks first.

First make sure the system can do this reliably:

1. run discovery
2. analyze failures
3. choose candidate family
4. validate candidate on future data
5. accept/reject

That road is the main product.
