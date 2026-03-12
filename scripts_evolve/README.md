# Round 0 Self-Evolving VSK Scaffold

这套脚手架先解决你原计划里的前三步：

1. 在 `testmini` 结果里切出 `discovery / val`
2. 把 baseline 结果 + traces 压成 `diagnostic cards`
3. 把一个结构化 patch proposal 落到 `generated_patches/` 并生成候选 config

## 推荐工作流

### Step A. 先跑一个 baseline
示例（5 题 smoke）：
```bash
python -m evaluation.generate_response \
  --agent visual_sketchpad \
  --model gpt-5-mini \
  --test_split_name testmini \
  --max_num_problems 5 \
  --output_dir results/gpt5mini_smoke5 \
  --output_file out.json \
  --vsk_keep_traces \
  --patch_config configs/active_patches.json \
  --patch_root generated_patches \
  --patch_debug_dump \
  --rerun
```

大规模 baseline（建议后面跑完整 `testmini` 或你指定数量）：
```bash
python -m evaluation.generate_response \
  --agent visual_sketchpad \
  --model gpt-5-mini \
  --test_split_name testmini \
  --output_dir results/round0_baseline \
  --output_file out.json \
  --vsk_keep_traces \
  --patch_config configs/active_patches.json \
  --patch_root generated_patches \
  --patch_debug_dump \
  --rerun
```

### Step B. 用 baseline 结果切 split
```bash
python scripts_evolve/split_testmini_from_results.py \
  --results-file results/round0_baseline/out.json \
  --out-json splits/mathvista_testmini_round0.json \
  --val-ratio 0.2 \
  --seed 42
```

输出：
- `splits/mathvista_testmini_round0.json`

### Step C. 生成 discovery diagnostic cards
```bash
python scripts_evolve/make_diagnostic_cards.py \
  --results-file results/round0_baseline/out.json \
  --traces-root results/round0_baseline/vsketchpad_traces \
  --split-json splits/mathvista_testmini_round0.json \
  --split-key discovery \
  --out-jsonl analysis/discovery_cards_round0.jsonl
```

如果你想做 val cards：
```bash
python scripts_evolve/make_diagnostic_cards.py \
  --results-file results/round0_baseline/out.json \
  --traces-root results/round0_baseline/vsketchpad_traces \
  --split-json splits/mathvista_testmini_round0.json \
  --split-key val \
  --out-jsonl analysis/val_cards_round0.jsonl
```

### Step D. 让 meta-agent 输出 patch proposal（下一步）
你后面可以把 `analysis/discovery_cards_round0.jsonl` 分批喂给 LLM，让它输出 proposal JSON：
```json
{
  "patch_name": "answer_format_numeric_only_v1",
  "patch_type": "prompt",
  "relpath": "prompts/answer_format_numeric_only_v1.txt",
  "content": "When the question expects a numeric answer, put the final answer as a bare number on the last line after ANSWER: ...",
  "enable": true
}
```

### Step E. 落地 proposal，生成 candidate config
```bash
python scripts_evolve/apply_patch_proposal.py \
  --proposal-json proposal.json \
  --patch-root generated_patches \
  --base-config configs/active_patches.json \
  --out-config configs/active_patches.candidate.json
```

然后 candidate 跑 val：
```bash
python -m evaluation.generate_response \
  --agent visual_sketchpad \
  --model gpt-5-mini \
  --test_split_name testmini \
  --output_dir results/round0_candidate_val \
  --output_file out.json \
  --vsk_keep_traces \
  --patch_config configs/active_patches.candidate.json \
  --patch_root generated_patches \
  --patch_debug_dump \
  --rerun
```

---

## 这套脚手架现在做到了什么
- discovery / val split 可重复
- diagnostic cards 可结构化导出
- patch proposal 可写入 patch root + 生成候选 config

## 还没自动化的部分
- cluster / summarize by batch
- LLM proposal generation
- val accept/reject 统计

如果你下一步继续，我建议先做：
1. 跑一个 20~50 题 baseline  
2. 生成 discovery cards  
3. 我再帮你做 `cluster + proposal + accept/reject` 那一套
