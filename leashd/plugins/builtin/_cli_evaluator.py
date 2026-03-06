"""Shared CLI evaluation utilities for AI-powered plugins.

Provides ``evaluate_via_cli()`` for one-shot Claude CLI evaluation and
``sanitize_for_prompt()`` for stripping invisible/control characters
from prompt content.
"""

from __future__ import annotations

import asyncio
import re

CONTROL_CHAR_RE = re.compile(
    "[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f"
    "\u0080-\u009f\u200b-\u200f\u2028\u2029"
    "\u202a-\u202e\u2060-\u2069\ufeff\ufff9-\ufffb]"
)


def sanitize_for_prompt(value: str) -> str:
    """Strip invisible/control chars that could break prompt structure.

    Removes C0 controls (except tab/newline/cr), C1 controls, zero-width
    chars, bidi marks, and line/paragraph separators.  Pattern from
    openclaw ``sanitizeForPromptLiteral()``.
    """
    return CONTROL_CHAR_RE.sub("", value)


async def evaluate_via_cli(
    system_prompt: str,
    user_message: str,
    *,
    model: str | None = None,
    timeout: float = 30.0,
) -> str:
    """Run a one-shot evaluation via ``claude -p``."""
    prompt = f"{system_prompt}\n\n{user_message}"
    cmd = ["claude", "-p", prompt, "--output-format", "text", "--max-turns", "1"]
    if model:
        cmd.extend(["--model", model])
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI error (exit {proc.returncode}): {stderr.decode()[:200]}"
        )
    return stdout.decode().strip()
