# Candidate Build Summary

- Parent version: `v1`
- Candidate version: `v1__extraction_family__round_003`
- Candidate type: `extraction_candidate`
- Candidate family: `extraction_family`
- Builder action: `build_extraction_candidate_v1`
- Candidate repo root: `/home/chengshengge/merge/CRADLE2/generated_candidates/v1__extraction_family__round_003/repo`
- Candidate patch config: `configs/active_patches.accepted_relation_compare_single_pass_v1.clean.json`

## Rationale
Dominant residuals look like answer extraction/output-format issues; improve extraction before changing reasoning.

## Modified files
- `evaluation/extract_answer.py`

## Build summary
Patched quick_extract to parse multiline ANSWER lines and robust multi-choice letters.
