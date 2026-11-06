"""Annotated single-GPU BF16 LoRA SFT for the existing question JSON.

Read README.md before renting a machine. This script trains the shared Economics
and rubric foundation. Prompted Grader behaviour is evaluated separately.
Academic method references are in README.md, with engineering choices labelled.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path

from data import DATA, GRAPH, data_report, encode_record, load_sources, padded_batch, sha256, split_records


def arguments() -> argparse.Namespace:
    """Read command-line options and return their named values.

    Example: --epochs 3 sets args.epochs to 3.0. Omitting an option uses the
    default shown below. --help lists every option without starting training.
    --check-data and --inspect are read-only inspection modes; otherwise this
    script runs the full training job. There is no shortened training mode.
    """
    p = argparse.ArgumentParser(description=__doc__)
    # Source locations and the exact pretrained checkpoint to adapt.
    p.add_argument("--data", type=Path, default=DATA)
    p.add_argument("--graph", type=Path, default=GRAPH)
    p.add_argument("--model", default="Qwen/Qwen3.5-9B-Base")
    p.add_argument("--revision", default="main", help="Prefer an immutable HF commit for a repeat experiment")
    p.add_argument("--output", type=Path, default=Path("runs/shared-economics"))
    p.add_argument("--val-series", default="2024-06")
    p.add_argument("--test-series", default="2025-06")
    p.add_argument("--check-data", action="store_true", help="Standard-library validation only, no download/GPU")
    p.add_argument("--inspect", action="store_true", help="Tokenize and report lengths, without loading model weights")
    # Memory and update frequency: micro-batch controls simultaneous examples,
    # while grad-accum controls how often their gradients update the adapter.
    p.add_argument("--max-length", type=int, default=4096)
    p.add_argument("--micro-batch", type=int, default=1)
    p.add_argument("--grad-accum", type=int, default=16)
    p.add_argument("--epochs", type=float, default=2.0)
    p.add_argument("--lr", type=float, default=5e-5)
    # LoRA rank controls adapter size. Alpha controls its scaling, and dropout
    # randomly removes some adapter inputs during training for regularisation.
    p.add_argument("--rank", type=int, default=16)
    p.add_argument("--alpha", type=int, default=32)
    p.add_argument("--dropout", type=float, default=0.05)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--resume", default=None, help="Path to a Trainer checkpoint from the same configuration")
    return p.parse_args()


def main() -> None:
    """Run validation, tokenization, LoRA training and adapter saving in order.

    Inputs come from arguments() and the existing question JSON. Outputs are
    Trainer checkpoints, the selected adapter, tokenizer files, a run manifest
    and GPU memory statistics under --output. The source JSON is never written.

    Heavy libraries are imported only when needed: --check-data works without
    Transformers/PyTorch, and --inspect loads a tokenizer but no model weights.
    """
    # 1. Validate the source files and make independent evaluation partitions.
    args = arguments()
    if min(args.max_length, args.micro_batch, args.grad_accum, args.rank, args.alpha) <= 0 or args.epochs <= 0 or args.lr <= 0:
        raise ValueError("Lengths, batches, epochs, learning rate, rank and alpha must be positive")
    records, graph = load_sources(args.data, args.graph)
    splits, dropped = split_records(records, args.val_series, args.test_series)
    report = data_report(records, graph, splits, dropped)
    report.update(data_sha256=sha256(args.data), graph_sha256=sha256(args.graph))
    print(json.dumps({k: v for k, v in report.items() if k != "split_ids"}, indent=2))
    if args.check_data:
        return  # This path deliberately has no ML dependency.

    # 2. Convert every complete example into token IDs and masked target labels.
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
    tokenizer.padding_side = "right"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    # No token packing: each example keeps its own boundary and attention mask.
    encoded = {name: [encode_record(tokenizer, r, args.max_length) for r in rows]
               for name, rows in splits.items()}
    report["token_lengths"] = {
        name: {"min": min(len(x["input_ids"]) for x in rows),
               "max": max(len(x["input_ids"]) for x in rows),
               "total": sum(len(x["input_ids"]) for x in rows),
               "supervised_tokens": sum(sum(t != -100 for t in x["labels"]) for x in rows)}
        for name, rows in encoded.items()
    }
    report["tokenizer_commit"] = tokenizer.init_kwargs.get("_commit_hash")
    print(json.dumps(report["token_lengths"], indent=2))
    if args.inspect:
        return

    # 3. Require one BF16-capable CUDA GPU before allocating model weights.
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import Qwen3_5ForConditionalGeneration, Trainer, TrainingArguments, set_seed
    if int(os.environ.get("WORLD_SIZE", "1")) != 1:
        raise ValueError("This recipe is validated structurally for one GPU. Launch with plain python, not torchrun.")
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("A CUDA GPU with BF16 support is required. No silent precision fallback.")
    set_seed(args.seed)
    torch.cuda.set_device(0)
    torch.cuda.reset_peak_memory_stats()
    args.output.mkdir(parents=True, exist_ok=True)
    if not args.resume and any(args.output.glob("checkpoint-*")):
        raise ValueError("Checkpoints already exist here. Use --resume or a new output directory.")

    # The official repo is multimodal. Load its actual architecture rather than
    # assuming a generic causal-LM checkpoint. This run supplies text only.
    # BF16 stores frozen weights in 16 bits: NO BitsAndBytes/4-bit/QLoRA is used.
    model = Qwen3_5ForConditionalGeneration.from_pretrained(
        args.model, revision=args.revision, dtype=torch.bfloat16,
        attn_implementation="sdpa", low_cpu_mem_usage=True,
    )
    model.config.use_cache = False  # Generation cache conflicts with checkpointed training.
    # All decoder linear projections are eligible, including Qwen's gated-delta
    # projections and MLPs. q_proj/v_proj alone would miss most hybrid layers.
    # Full module names prevent accidentally adding adapters to the vision tower.
    targets = [name for name, module in model.named_modules()
               if ".language_model.layers." in f".{name}" and isinstance(module, torch.nn.Linear)]
    if not targets:
        raise RuntimeError("No text decoder linear modules found. Inspect the installed model architecture.")
    # 4. Add the trainable LoRA matrices; the original model stays frozen.
    config = LoraConfig(
        task_type="CAUSAL_LM", target_modules=targets,
        r=args.rank, lora_alpha=args.alpha, lora_dropout=args.dropout,
        bias="none",  # Frozen base weights plus small trainable low-rank matrices.
    )
    model = get_peft_model(model, config)
    # Non-reentrant checkpointing recomputes activations during backward [Chen].
    # Input grads allow gradients to reach LoRA despite frozen input embeddings.
    model.enable_input_require_grads()
    model.accepts_loss_kwargs = False
    trainable = [(n, p) for n, p in model.named_parameters() if p.requires_grad]
    if not trainable or any("lora_" not in n or ".visual." in n for n, _ in trainable):
        raise RuntimeError("Unexpected trainable base/vision parameters")
    model.print_trainable_parameters()

    class Collator:
        """Bridge variable-length dataset examples and a tensor batch for Trainer.

        Trainer calls this object for each batch. It pads only that batch, then
        converts token IDs, masks and labels to PyTorch integer tensors.
        """

        def __call__(self, features: list[dict[str, list[int]]]) -> dict[str, torch.Tensor]:
            """Return [batch_size, padded_length] tensors, preserving -100 labels.

            features is a list of encode_record outputs. Trainer moves the
            returned tensors to the training device before calling the model.
            """
            batch = padded_batch(features, tokenizer.pad_token_id)
            return {key: torch.tensor(value, dtype=torch.long) for key, value in batch.items()}

    train_rows = encoded["train"]

    # 5. Configure the full training run and checkpoint selection.
    # Effective batch = micro_batch * grad_accum * number_of_GPUs.
    # Default: 1 * 16 * 1 = 16 examples per optimizer update (last may be smaller).
    # Accumulation reduces activation memory vs loading 16 examples together.
    # These numeric settings are starting hypotheses, not a paper's optimum.
    settings = TrainingArguments(
        output_dir=str(args.output), seed=args.seed, data_seed=args.seed,
        per_device_train_batch_size=args.micro_batch,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.grad_accum,
        num_train_epochs=args.epochs,
        learning_rate=args.lr, lr_scheduler_type="linear", warmup_ratio=0.1,
        optim="adamw_torch", weight_decay=0.01, max_grad_norm=1.0,
        bf16=True, fp16=False,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        eval_strategy="epoch", save_strategy="epoch",
        save_total_limit=2, load_best_model_at_end=True,
        metric_for_best_model="eval_loss", greater_is_better=False,
        prediction_loss_only=True,  # Avoid accumulating huge vocabulary logits at evaluation.
        logging_steps=1, report_to="none", dataloader_num_workers=0,
        remove_unused_columns=False,
    )
    report.update(
        recipe={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        model_commit=getattr(model.config, "_commit_hash", None),
        gpu=torch.cuda.get_device_name(0),
        effective_batch=args.micro_batch * args.grad_accum,
        trainable_parameters=sum(p.numel() for _, p in trainable),
        lora_target_modules=targets,
        packages={name: importlib.metadata.version(name) for name in ("torch", "transformers", "peft", "accelerate")},
    )
    # This is a small audit manifest with IDs and settings, not another SFT dataset.
    (args.output / "run_manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    # 6. Trainer performs forward passes, loss calculation, backpropagation,
    # accumulation and optimizer updates. Validation does not update weights.
    trainer = Trainer(model=model, args=settings, train_dataset=train_rows,
                      eval_dataset=encoded["validation"], data_collator=Collator(),
                      processing_class=tokenizer)
    trainer.train(resume_from_checkpoint=args.resume)
    # load_best_model_at_end has restored the lowest-validation-loss checkpoint.
    # For a PEFT model, save_model saves the adapter, not another full 9B model.
    trainer.save_model(str(args.output / "adapter"))
    tokenizer.save_pretrained(args.output / "adapter")
    memory = {"peak_allocated_GiB": torch.cuda.max_memory_allocated() / 2**30,
              "peak_reserved_GiB": torch.cuda.max_memory_reserved() / 2**30}
    (args.output / "memory.json").write_text(json.dumps(memory, indent=2), encoding="utf-8")
    print(json.dumps(memory, indent=2))
    print("Saved best validation-loss adapter")
    # Test generation is a separate command so test outcomes do not drive training.


if __name__ == "__main__":
    main()
