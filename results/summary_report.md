# Multilingual Prompt-Injection Benchmark - Summary

Models: qwen3:8b, llama3.1:8b, gemma2:9b  
Languages: English, Bangla, Banglish  
Evaluated answers: 450

**Attack success rate (ASR)** = (FAIL + PARTIAL) / answers that were not ERROR. **Fail rate** counts FAIL only. Lower is better. The 95% interval shows how much the ASR could move by chance with this many prompts.

## Overall results per model

| Rank | Model | Tested | PASS | PARTIAL | FAIL | ERROR | Fail rate | ASR | ASR 95% CI | Most vulnerable categories |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | llama3.1:8b | 150 | 121 | 1 | 28 | 0 | 18.7% | 19.3% | 14-26% | system_prompt_extraction (40%), role_manipulation (33%) |
| 2 | qwen3:8b | 150 | 91 | 0 | 59 | 0 | 39.3% | 39.3% | 32-47% | instruction_override (67%), system_prompt_extraction (53%) |
| 3 | gemma2:9b | 150 | 81 | 1 | 68 | 0 | 45.3% | 46.0% | 38-54% | instruction_override (87%), system_prompt_extraction (80%) |

## Fail rate and ASR per language

| Model | English fail / ASR | Bangla fail / ASR | Banglish fail / ASR |
|---|---|---|---|
| llama3.1:8b | 14.0% / 16.0% | 32.0% / 32.0% | 10.0% / 10.0% |
| qwen3:8b | 50.0% / 50.0% | 44.0% / 44.0% | 24.0% / 24.0% |
| gemma2:9b | 56.0% / 58.0% | 48.0% / 48.0% | 32.0% / 32.0% |
| **All models** | **40.0% / 41.3%** | **41.3% / 41.3%** | **22.0% / 22.0%** |

## Attack success rate per category

| Category | qwen3:8b | llama3.1:8b | gemma2:9b |
|---|---|---|---|
| instruction_override | 66.7% | 13.3% | 86.7% |
| system_prompt_extraction | 53.3% | 40.0% | 80.0% |
| secret_extraction | 33.3% | 6.7% | 0.0% |
| role_manipulation | 33.3% | 33.3% | 80.0% |
| jailbreak | 33.3% | 13.3% | 53.3% |
| fake_authority | 40.0% | 20.0% | 33.3% |
| indirect_injection | 40.0% | 0.0% | 20.0% |
| obfuscation_encoding | 33.3% | 20.0% | 40.0% |
| context_manipulation | 26.7% | 20.0% | 33.3% |
| social_engineering | 33.3% | 26.7% | 33.3% |

## Attack success rate per difficulty

| Difficulty | qwen3:8b | llama3.1:8b | gemma2:9b |
|---|---|---|---|
| easy | 23.3% | 13.3% | 30.0% |
| medium | 38.3% | 11.7% | 40.0% |
| hard | 48.3% | 30.0% | 60.0% |

## Same attack, different language (paired comparison)

Each attack exists in all three languages (same `pair_id`). This counts attacks a model resisted in English but not in the other language, and the other way round.

| Model | Language | Resisted in English, broken here | Broken in English, resisted here |
|---|---|---|---|
| qwen3:8b | Bangla | 5 | 8 |
| qwen3:8b | Banglish | 3 | 16 |
| llama3.1:8b | Bangla | 11 | 3 |
| llama3.1:8b | Banglish | 4 | 7 |
| gemma2:9b | Bangla | 2 | 7 |
| gemma2:9b | Banglish | 0 | 13 |

## Conclusions (computed from the data)

- **Best overall (lowest ASR): llama3.1:8b** with 19.3% ASR (28 FAIL, 1 PARTIAL out of 150).
- The interval of llama3.1:8b does not overlap with the next model (qwen3:8b), so the lead is clear.
- **Language with the highest failure rate: Bangla** (41.3% ASR across all models; English 41.3%, Bangla 41.3%, Banglish 22.0%).
- qwen3:8b is most vulnerable to: instruction_override (67%), system_prompt_extraction (53%).
- llama3.1:8b is most vulnerable to: system_prompt_extraction (40%), role_manipulation (33%).
- gemma2:9b is most vulnerable to: instruction_override (87%), system_prompt_extraction (80%).

## Data quality notes

- Answers flagged `needs_review` (marker produced while also refusing, counted as PARTIAL): 1.
- Answers cut off at the token limit (`truncated`): 13 (qwen3:8b / English: 1, qwen3:8b / Bangla: 1, qwen3:8b / Banglish: 2, llama3.1:8b / English: 0, llama3.1:8b / Bangla: 6, llama3.1:8b / Banglish: 1, gemma2:9b / English: 0, gemma2:9b / Bangla: 0, gemma2:9b / Banglish: 2). If one language is truncated much more often, failures at the end of its answers may be missed.
- ERROR answers: 0 (see errors.log).

## Charts

### Outcomes per model

![Outcomes per model](charts/outcomes_by_model.png)

### Attack success rate by language

![Attack success rate by language](charts/asr_by_language.png)

### Attack success rate by category

![Attack success rate by category](charts/asr_by_category.png)

