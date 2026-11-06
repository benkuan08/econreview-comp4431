# Training results and code

Training completed successfully: 2 epochs, 26 optimizer updates, best validation loss 0.758238.
This folder contains the selected trained LoRA adapter weights, tokenizer, code, logs and result records.
The 9B base-model weights remain on the GPU server and were not downloaded to this computer.

Base model: /root/autodl-tmp/COMP4431/models/Qwen3.5-9B-Base
Final LoRA adapter: /root/autodl-tmp/COMP4431/runs/shared-economics/adapter

The two-step smoke check passed. Saved adapter tensors were verified finite.
A single held-out generation check returned valid JSON but used a concept ID absent from the knowledge graph.
Actual Economics grading accuracy has not been evaluated. Treat the adapter as experimental.

run_summary.json contains the main results. environment.txt records the installed packages.
code/ contains the training code and prompts used. runtime/ contains the one-off launch code.
The source question JSON and graph in the workspace were not changed.

Public Hugging Face repository: https://huggingface.co/benjaik/igcse-economics-qwen3.5-9b-lora-20261105

The adapter and every downloaded file were verified by SHA256. Local HTTPS download took about 120 seconds.
