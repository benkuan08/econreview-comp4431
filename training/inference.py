"""Shared inference helpers used by evaluate.py and predict_role.py.

Training is handled by train_lora.py. These functions load an already-trained
adapter (or the unchanged base model) to generate answers after training.
"""

from data import ROOT, prompt_ids


def load_model(model_id: str, revision: str = "main", adapter: str | None = None) -> tuple:
    """Load the base model, optionally attach an adapter, and enable inference.

    Args:
        model_id: Hugging Face repository name or local base-model folder.
        revision: Repository branch/commit. Reuse the training commit for a fair
            comparison; prefer a fixed commit over a moving branch such as main.
        adapter: Saved LoRA adapter folder, or None to evaluate the base alone.
    Returns:
        (model, tokenizer), ready to generate on a single CUDA GPU.
    Raises:
        RuntimeError if CUDA or BF16 support is unavailable. Loading can also
        fail if model files are unavailable or the adapter uses a different base.

    No optimizer is created. model.eval() disables training-time dropout, and
    is_trainable=False keeps the loaded adapter frozen during evaluation.
    """
    # Import GPU libraries here so importing this module for data checks is cheap.
    import torch
    from transformers import AutoTokenizer, Qwen3_5ForConditionalGeneration
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("Use a CUDA GPU with BF16 support")
    # Use the original base model/tokenizer for a fair before/after comparison.
    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
    model = Qwen3_5ForConditionalGeneration.from_pretrained(
        model_id, revision=revision, dtype=torch.bfloat16,
        attn_implementation="sdpa", low_cpu_mem_usage=True,
    )
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter, is_trainable=False)
    model.to("cuda").eval()
    model.config.use_cache = True  # Reuse past states while generating each new token.
    return model, tokenizer


def generate(model, tokenizer, conversation: list[dict[str, str]],
             max_new_tokens: int = 2048, max_context: int = 8192) -> tuple[str, bool]:
    """Generate one deterministic response without modifying model weights.

    Args:
        model/tokenizer: Matching objects returned by load_model.
        conversation: System/user messages containing the task and its context.
        max_new_tokens: Upper limit on response length, excluding the input.
        max_context: This helper's total input-plus-response budget in tokens.
            It is a local guard, not a claim about Qwen's maximum context size.
    Returns:
        (text, stopped_normally). False means no end token was generated, usually
        because the response reached its token limit. Check this before use.
    Raises:
        ValueError if the input plus reserved response would exceed the budget.

    do_sample=False selects greedy decoding. This makes the decoding choice
    deterministic, although exact GPU results may vary across environments.
    """
    import torch
    ids = prompt_ids(tokenizer, conversation)
    if len(ids) + max_new_tokens > max_context:
        raise ValueError("Input and generation budget exceed max_context. No truncation performed.")
    # [ids] introduces a batch dimension: shape [1, input_token_count].
    tensor = torch.tensor([ids], dtype=torch.long, device="cuda")
    stop = tokenizer.convert_tokens_to_ids("<|im_end|>")
    # No gradients are needed for inference, reducing memory and computation.
    with torch.inference_mode():
        result = model.generate(
            input_ids=tensor, attention_mask=torch.ones_like(tensor),
            do_sample=False, max_new_tokens=max_new_tokens,
            eos_token_id=[stop, tokenizer.eos_token_id],
            pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
        )
    # generate returns input tokens followed by new tokens. Remove the input
    # prefix so the returned text contains only the assistant's response.
    new_ids = result[0, len(ids):].tolist()
    stopped = bool(new_ids and new_ids[-1] in (stop, tokenizer.eos_token_id))
    return tokenizer.decode(new_ids, skip_special_tokens=True).strip(), stopped


def role_prompt(role: str) -> str:
    """Read one role's existing system prompt from the workspace Prompts folder.

    role is a CLI-validated name such as 'grader' or 'probe_judge'. Return the
    prompt text without leading/trailing whitespace; do not change the file.
    """
    return (ROOT / "Prompts" / f"{role}.txt").read_text(encoding="utf-8").strip()
