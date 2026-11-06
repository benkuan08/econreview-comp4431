# Training the shared IGCSE Economics model

Use `question_records.training.json` directly. This folder does **not** create another SFT dataset. The loader formats each existing record in memory and preserves every marking point, acceptable alternative and rejected claim. The knowledge graph is checked for valid links and used for later probing and evaluation. Its probes are not added to training.

The model is **Qwen/Qwen3.5-9B-Base**, with one shared **BF16 LoRA** adapter. Grader, Diagnoser, Prober, Probe-Judge and Tutor use that same adapted model with different prompts in `Prompts/`. Common economic mistakes learned from `reject` and marking guidance can help multiple roles. That transfer is an experimental hypothesis which needs evaluation.

## What the existing records supervise

Each training input contains the original `q_id`, `question` and `marks`. Its assistant target contains the original `q_id`, `marks`, `marking_points`, `accept` and `reject`. No target fields are invented. Only the repeated question text is omitted from the response. The original JSON remains untouched.

This teaches Economics and rubric generation, including common errors. The file contains no student attempts or gold grading decisions, so the resulting adapter has **not yet demonstrated grading accuracy**. Grader is the first role to evaluate and calibrate. Prompting the other roles does not guarantee their reliability. The supplied role prompts always ground grading and diagnosis in the relevant rubric, graph and student evidence.

The 103 holistic questions require best-fit levels. Their diagnostic marking points must not be added up as marks. The 29 calculation questions retain answer-only, units, sign and partial-credit rules verbatim. Do not replace those rules with a generic “one point = one mark” algorithm.

## Which AutoDL card to choose

**Recommended: one RTX PRO 6000 Blackwell 96 GB (`PRO6000-96G` on AutoDL).** It should have ample memory for this 9B BF16 LoRA recipe with microbatch 1 and a 4,096-token limit. An A100/A800 80 GB is an alternative, rather than a requirement. A 48 GB L20, RTX A6000 or RTX 6000 Ada is another possible option, subject to actual memory use. Verify the listed VRAM: RTX 6000 Ada has 48 GB, whereas RTX PRO 6000 Blackwell has 96 GB. [NVIDIA specifications](https://www.nvidia.com/en-gb/products/workstations/professional-desktop-gpus/rtx-pro-6000/), [AutoDL GPU list](https://www.autodl.com/docs/instance_pro_api/).

AutoDL data-disk expansion increases space for model files and checkpoints. It does not increase GPU VRAM. [AutoDL storage documentation](https://www.autodl.com/docs/local_disk/).

A 24 GB RTX 4090 is a tight experimental option for this BF16 training recipe. Nine billion weights alone take approximately 18 GB in decimal units, before activations, logits, adapters, optimizer state and CUDA overhead. The large output vocabulary adds memory pressure. Treat 24 GB training as requiring a measured configuration, rather than a promised fit. This estimate is arithmetic, not a measured peak. Inference and training have different memory requirements.

At the 5 November 2026 check, [AutoDL's creation page](https://www.autodl.com/create) redirected to login. Live stock, hourly prices and training time were not verified. Compare the actual offers in your account. No machine has been rented. Select Linux, Python 3.11, a driver compatible with the chosen CUDA wheel, at least 64 GB host RAM and about 80 GB free disk for model cache and runs. Keep outputs on persistent storage. These are engineering recommendations, not vendor requirements.

## Files

| File | Purpose |
|---|---|
| `data.py` | Read and validate the existing JSON, split by exam series, format in memory, mask labels |
| `train_lora.py` | Annotated BF16 LoRA training, token inspection and checkpoints |
| `evaluate.py` | Before/after held-out rubric generation and graph-probe transfer outputs |
| `predict_role.py` | Offline experiments using any of the five role prompts |
| `inference.py` | Shared evaluation loader and generation settings |

`train_lora.py` and `data.py` run the training pipeline. `evaluate.py`, `predict_role.py` and their shared helper `inference.py` are for use after training. They are retained so you can evaluate the adapter and try the prompted roles.

## How to read the functions

Every function has a docstring immediately below its definition, explaining its purpose, inputs and returned values. Type hints such as `max_length: int` describe the expected kind of value. A return hint such as `-> tuple[str, bool]` means the function returns two values: text and a true/false flag. Comments inside functions explain the less obvious operations.

Read the training code in this order:

1. `train_lora.arguments()` reads options such as epochs and learning rate from your command.
2. `data.load_sources()` reads the existing question JSON and graph, then validates their structure and links.
3. `data.split_records()` selects training, validation and test questions without changing the JSON file.
4. `data.messages()` makes the system instruction, user question and assistant target in memory.
5. `data.prompt_ids()` applies Qwen's chat template. `data.encode_record()` adds the target tokens and masks prompt labels with -100, so loss is calculated on the assistant response.
6. `Collator.__call__()` uses `data.padded_batch()` to pad a batch and convert its lists to PyTorch tensors.
7. `train_lora.main()` loads the frozen BF16 model, attaches LoRA, configures Trainer, runs training and saves the selected adapter.

For use after training, `inference.load_model()` loads the base and optional saved adapter, `inference.generate()` produces one response, and `inference.role_prompt()` reads a role's instructions. `evaluate.rubric_checks()` checks generated JSON structure and concept IDs. It does not judge the economic meaning of an answer. The `main()` functions in `evaluate.py` and `predict_role.py` connect those helpers to their command-line options.

## Training settings and what they mean

| Setting | Starting value | Explanation |
|---|---|---|
| Precision | BF16 frozen base and mixed precision compute | Ordinary 16-bit weights, with floating-point adapter/optimizer state. No quantization or bitsandbytes. [1, 2] |
| LoRA rank and alpha | 16 and 32 | Train small matrices on text decoder linear layers. The usual scaling is alpha/r = 2. Vision, embeddings and output head remain frozen. [1] |
| LoRA dropout | 0.05 | Regularization starting point for a small corpus |
| Microbatch | 1 | One example processed at a time on the GPU |
| Gradient accumulation | 16 | Accumulate gradients before updating weights. Effective batch is 1 × 16 × 1 GPU = 16 examples, except a smaller final batch |
| Epochs | 2 | Two passes over the training split, around 26 optimizer updates with the current 208 records |
| Learning rate | 0.00005 | Conservative starting point. Compare validation behaviour before changing |
| Optimizer | AdamW | Weight decay 0.01 and gradient clipping at norm 1.0. [3] |
| Schedule | Linear, warmup 10% | Roughly 3 warmup steps in this small first run |
| Maximum length | 4096 tokens | Prompt plus complete answer. Overlong records stop the run instead of truncating grading rules |
| Checkpointing | Enabled, non-reentrant | Recompute activations during backward to reduce memory. [4] |
| Loss | Assistant tokens only | Prompt and padding labels are -100. The response and chat end token are supervised |
| Validation | Each epoch | Select the checkpoint with lowest validation loss. Test generation remains separate |

The papers support the methods. **Rank 16, learning rate, batch size and two epochs are engineering starting values, not academically established optimal values for this dataset.** With only about 26 optimizer updates, this is an exploratory run. Check validation loss and expert-reviewed outputs, and repeat with multiple seeds when comparing configurations. Increasing epochs can increase memorisation.

The official checkpoint is a multimodal architecture. The loader uses `Qwen3_5ForConditionalGeneration`, supplies text only and targets linear layers inside `model.language_model.layers`. This includes the hybrid gated-delta projections and feed-forward layers. Adapting only `q_proj` and `v_proj` would omit many hybrid layers. No image training is performed. The ordinary PyTorch fallback for gated-delta operations may be slow. Optional accelerated kernels require a separately tested environment.

## Install and run on AutoDL

Copy this workspace's JSON files, `training/` and `Prompts/` to the machine. Source PDFs are unnecessary for running this recipe. Run these commands from the copied workspace root in a Linux terminal. This is a proposed reproducible stack. GPU execution has not been tested on this Windows laptop.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
# Official PyTorch CUDA 12.8 wheel. The machine driver must support it.
python -m pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r training/requirements.txt
python -m pip check
python -c "import torch; print(torch.__version__, torch.cuda.is_available()); print(torch.cuda.get_device_name(0)); print('BF16:', torch.cuda.is_bf16_supported())"
```

The CUDA wheel must match the driver's capabilities. Use [PyTorch's official installation instructions](https://pytorch.org/get-started/previous-versions/) if the selected image needs a different supported wheel. Do not silently replace BF16 with 4-bit loading. Hugging Face must be reachable, or use a complete locally downloaded copy of the official model as `--model /path/to/model`.

```bash
# 1. No model download: validate the real source files.
python training/train_lora.py --check-data

# 2. Tokenizer only: inspect actual prompt+response lengths before loading weights.
python training/train_lora.py --inspect

# 3. Run full training and save the selected adapter.
python training/train_lora.py --output runs/shared-economics
```

The trainer runs for the requested epochs and validates after each epoch. Monitor `nvidia-smi` during training. On successful completion, `runs/shared-economics/memory.json` records peak allocated and reserved GPU memory for that run. If memory is insufficient, choose a larger-memory card or lower the maximum length only when `--inspect` confirms every complete record still fits. Saving a checkpoint and resuming can recover interrupted training:

```bash
python training/train_lora.py --output runs/shared-economics --resume runs/shared-economics/checkpoint-13
```

The checkpoint number above is illustrative. Use the path actually created. Keep the same source hashes, model revision, split and hyperparameters when resuming. For repeatability, pass the official model's immutable commit via `--revision COMMIT_SHA` in **all** train/evaluation commands. The run manifest records the resolved model/tokenizer revision, package versions, GPU, target modules, source hashes and split IDs. Record `python -m pip freeze` and `nvidia-smi` with the run when publishing results. Adapters need the same base model to be used later.

## Split and leakage policy

The current 272 records cover 19 paper sets and 11 exam series. June 2024 is validation and June 2025 is test. Standard and R papers in a series stay together. Exact duplicate question text plus marks is excluded from the earlier split, with test taking priority. Current outcome: **208 train, 29 validation, 27 test, 8 excluded duplicate records**. All 272 remain in the source JSON. The exclusions are recorded by ID in the run manifest. This catches exact repetitions, not all semantically similar questions.

Do not train on validation/test targets while reporting them as held out. After model selection and the final test, a separate explicitly labelled all-data deployment run could be considered, but it would require an independent evaluation set. This recipe keeps the evaluation split intact.

## Evaluate the shared model

Compare the unchanged base and the adapted model with the same revision, prompts and decoding settings. Validation outputs may guide development. Reserve test for a final comparison. Use separate processes to avoid holding two models in GPU memory.

```bash
python training/evaluate.py --split validation --output runs/base-validation.jsonl
python training/evaluate.py --split validation --adapter runs/shared-economics/adapter --output runs/adapted-validation.jsonl
python training/evaluate.py --output runs/base-test.jsonl
python training/evaluate.py --adapter runs/shared-economics/adapter --output runs/adapted-test.jsonl

# Transfer to concept questions from the graph. These probes were not added to SFT.
python training/evaluate.py --task probes --output runs/base-probes.jsonl
python training/evaluate.py --task probes --adapter runs/shared-economics/adapter --output runs/adapted-probes.jsonl
```

The automatic summary checks JSON structure, known concept IDs and normal stopping. It does **not** claim that valid JSON is a correct rubric. An Economics reviewer should assess correctness, valid alternatives, common errors, calculation rules and holistic levels. Do not use exact wording match as the main correctness metric. Graph probes assess transfer to another task. Their concepts may already appear in trained rubrics.

For the first **Grader** evaluation, supply real or independently reviewed student answers in a local payload with the question record and `student_answer`, then compare predictions to independent gold grades. Include blank, partially correct and fully correct answers, equivalent wording, valid alternative reasoning, explicit misconceptions, missing application, and units/answer-only calculation cases. Include holistic answers at each available level. Split all attempts for a question together. Reviewers should not see the model output when assigning gold labels.

```bash
python training/predict_role.py --role grader --input path/to/reviewed_attempt_payload.json --adapter runs/shared-economics/adapter
```

This is a command example, not a newly fabricated student attempt. Use the other role names `diagnoser`, `prober`, `probe_judge` and `tutor` with their corresponding supplied context. The prompts define the JSON contracts. Backend validation and conversation state are specified in the design document and remain future work.

Report exact-score agreement, mean absolute error and quadratic weighted kappa for actual grades, together with sample sizes and confidence intervals. Check per-point evidence and false misconception accusations. For the prompted roles, review diagnosis against evidence, graph-valid probe selection, Probe-Judge agreement and whether a fresh post-teaching check improves performance. Do not infer those results from training loss.

## Academic basis and implementation references

1. Hu et al. (2021), [LoRA](https://arxiv.org/abs/2106.09685). Frozen pretrained weights with trainable low-rank updates. Supports the adaptation method, not this dataset's numerical hyperparameters.
2. Kalamkar et al. (2019), [A Study of BFLOAT16 for Deep Learning Training](https://arxiv.org/abs/1905.12322). Supports BF16 as a training precision choice. It does not establish local GPU memory usage.
3. Loshchilov and Hutter (2019), [Decoupled Weight Decay Regularization](https://arxiv.org/abs/1711.05101). Basis for AdamW.
4. Chen et al. (2016), [Training Deep Nets with Sublinear Memory Cost](https://arxiv.org/abs/1604.06174). Basis for activation recomputation and its memory/computation tradeoff.
5. Ouyang et al. (2022), [Training language models to follow instructions with human feedback](https://arxiv.org/abs/2203.02155). Uses supervised demonstrations as part of instruction training. This recipe uses SFT only and does not reproduce RLHF.
6. Zhou et al. (2023), [LIMA](https://arxiv.org/abs/2305.11206). Motivates evaluating transfer after curated SFT. Its 65B model and 1,000 examples do not establish that this 9B rubric corpus is sufficient for grading.
7. [Official Qwen base checkpoint](https://huggingface.co/Qwen/Qwen3.5-9B-Base), [Transformers Qwen3.5 documentation](https://huggingface.co/docs/transformers/model_doc/qwen3_5), [pinned v5.3.0 implementation](https://github.com/huggingface/transformers/blob/v5.3.0/src/transformers/models/qwen3_5/modeling_qwen3_5.py) and [PEFT LoRA documentation](https://huggingface.co/docs/peft/developer_guides/lora).
8. [NVIDIA RTX PRO 6000 Blackwell specifications](https://www.nvidia.com/en-gb/products/workstations/professional-desktop-gpus/rtx-pro-6000/) and [AutoDL GPU list](https://www.autodl.com/docs/instance_pro_api/). AutoDL availability and cost must be checked in the user's logged-in account.

## What was verified locally

The source-data validation passes against the unchanged JSON files, and all remaining Python files compile. No tokenizer/model download, GPU training, memory peak, training time or grading benchmark has been completed locally. Use `--inspect` to check actual token lengths on the selected machine before training.
