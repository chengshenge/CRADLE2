# pid11 Membership-Verification Hook Spec v1

## Why this new family exists
The rejected subtraction hook family was too generic:
- it enforced a subtraction workflow,
- but it did not reliably verify object membership in each removal set,
- and it failed to solve pid 11.

Repeated evaluation showed:
- pid 11 remains unresolved under both hook-on and nohook settings,
- so the next mechanism must target object-set membership explicitly.

## Target failure mechanism
This family is for questions of the form:
- subtract/remove/exclude/count remaining
where correctness depends on determining whether each visible object belongs to one or more attribute-defined removal sets.

Representative difficulty:
- overlapping removal conditions
- same object satisfying multiple conditions
- attribute verification errors (red vs not red, tiny vs not tiny, matte vs glossy, ball vs non-ball)
- grounded counting after set union

## Scope
Apply only to:
- subtraction / remove / remaining-count questions
- where answer depends on object-set membership

Do NOT apply to:
- comparison relation questions
- scale / measurement questions
- pure arithmetic text-only subtraction
- non-visual counting without attribute filtering

## Core idea
Do not directly "do subtraction".
First build a grounded membership table.

## Required workflow
1. Enumerate visible candidate objects with short grounded handles.
   - Example style: object_1 = small red ball near center-right
   - Keep ordering stable: left-to-right, then top-to-bottom if possible

2. For each candidate object, evaluate the following attributes explicitly:
   - is_red
   - is_tiny
   - is_matte
   - is_ball

3. Build removal-set membership explicitly:
   - in_red_set
   - in_tiny_matte_ball_set

4. Compute:
   - removed = union(red_set, tiny_matte_ball_set)
   - remaining = visible_objects - removed

5. Only after the table is complete, produce:
   - visible_count
   - removed_count
   - remaining_count
   - final_answer

## Output schema requirement
The hook should force a compact structured output with fields like:

{
  "visible_objects": [
    {"name": "...", "is_red": true/false, "is_tiny": true/false, "is_matte": true/false, "is_ball": true/false,
     "in_red_set": true/false, "in_tiny_matte_ball_set": true/false, "removed": true/false}
  ],
  "visible_count": <int>,
  "removed_count": <int>,
  "remaining_count": <int>,
  "final_answer": <int>
}

The model may still write natural language internally, but the forced answer protocol should reduce freedom near the end.

## Hard constraints
- Do not remove the same physical object twice.
- Do not infer membership without grounded visual evidence.
- Do not switch into comparison-style reasoning.
- Do not skip the membership table and jump directly to a count.
- If attribute confidence is uncertain, mark that uncertainty explicitly before deciding membership.

## Acceptance targets
This family is considered promising only if:
- on target-only repeated evaluation, pid 11 beats the current nohook baseline by majority,
- and it does not create majority regression on protected targets.

## Protected targets for first-pass screening
- pid 12 (comparison relation)
- pid 19 (measurement target)

## First implementation philosophy
Keep implementation minimal:
- one prompt-level hook only at first,
- no new tool backend,
- no extra complex controller logic,
- just enough structure to test whether explicit membership tables reduce pid 11 variance/error.

## Non-goals for v1
- solving measurement-target problems
- solving all subtraction/counting questions
- adding new vision models
- redesigning the entire patch stack
