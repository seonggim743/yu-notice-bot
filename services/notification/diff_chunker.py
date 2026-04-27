"""
Diff text chunking shared by Telegram and Discord notifiers.

Both notifiers need to split a diff into segments that fit their platform
length limit (Discord embed field: 1024 chars; Telegram message: 4096 chars).
The previous per-notifier implementations sliced blindly by character count;
this module preserves line boundaries when possible so a chunk does not
break in the middle of a `+`/`-` line.
"""
from typing import List


def split_diff(diff_text: str, max_length: int) -> List[str]:
    """
    Split diff_text into chunks no longer than max_length characters.

    Lines (delimited by \\n) are kept intact whenever possible. A line
    longer than max_length on its own is hard-split at the character
    boundary as a last resort.

    Args:
        diff_text: Text to split. Empty string returns [].
        max_length: Maximum characters per chunk. Must be > 0.

    Returns:
        List of chunks, each <= max_length characters. Concatenating them
        reproduces the original text.
    """
    if not diff_text:
        return []
    if max_length <= 0:
        raise ValueError("max_length must be positive")
    if len(diff_text) <= max_length:
        return [diff_text]

    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for line in diff_text.splitlines(keepends=True):
        if len(line) > max_length:
            # Flush whatever we have buffered before hard-splitting.
            if current:
                chunks.append("".join(current))
                current = []
                current_len = 0
            for i in range(0, len(line), max_length):
                chunks.append(line[i : i + max_length])
            continue

        if current_len + len(line) > max_length:
            chunks.append("".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)

    if current:
        chunks.append("".join(current))

    return chunks
