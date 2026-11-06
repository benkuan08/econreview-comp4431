---
base_model: Qwen/Qwen3.5-9B-Base
library_name: peft
language:
- en
tags:
- lora
- economics
- education
---
# IGCSE Economics shared LoRA adapter

Experimental shared Economics and rubric adapter for Qwen3.5-9B-Base.
This repository contains the selected LoRA adapter and tokenizer, not the base model.
See source_model.json for the exact base revision and training_records.tar.gz for the run records and code.

Trained on the existing question-record corpus for two epochs, with 208 training,
29 validation and 27 test questions, excluding eight earlier exact repetitions.
Microbatch 4 and gradient accumulation 4 give an effective batch of 16.
Best validation loss was 0.758238. Actual student grading accuracy has not been evaluated.
A one-record generation check returned valid JSON but used a concept ID absent from the graph.
The adapter therefore remains experimental and requires grounded prompts and further evaluation.
