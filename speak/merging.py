"""Conservative cloud overlap merging: never truncate the accumulated transcript."""
import re

EDGE_PUNCTUATION = ".,!?;:，。！？；：、"


def _normalized(text):
    chars, ends = [], []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        chars.append(char)
        ends.append(index + 1)
    while chars and chars[0] in EDGE_PUNCTUATION:
        chars.pop(0); ends.pop(0)
    while chars and chars[-1] in EDGE_PUNCTUATION:
        chars.pop(); ends.pop()
    return "".join(chars), ends


def merge_cloud_text(previous, incoming, overlap_frames=0):
    """Return (text, needs_review). Ambiguous/repeated/short matches retain both sides."""
    if not incoming:
        return previous, False
    if not previous:
        return incoming, False
    separator = " " if previous[-1:].isascii() and previous[-1:].isalnum() and incoming[:1].isascii() and incoming[:1].isalnum() else ""
    if not overlap_frames:
        return previous + separator + incoming, False
    tail, _ = _normalized(previous[-100:])
    head, ends = _normalized(incoming[:100])
    # At most 2 seconds of overlapping speech: do not match arbitrarily long phrases.
    for size in range(min(len(tail), len(head), 32), 3, -1):
        match = head[:size]
        if not tail.endswith(match):
            continue
        if size == len(head):
            continue  # Entire repeated utterance vs. overlap-only output is ambiguous.
        # Avoid deduplicating a partial ASCII identifier or obvious repeated utterance.
        if (len(head) > size and re.fullmatch(r"[A-Za-z0-9_]", head[size-1])
                and re.fullmatch(r"[A-Za-z0-9_]", head[size])):
            continue
        if (len(tail) > size and re.fullmatch(r"[A-Za-z0-9_]", tail[-size])
                and re.fullmatch(r"[A-Za-z0-9_]", tail[-size-1])):
            continue
        if (len(tail) > size and tail[-size].isdigit() and tail[-size-1] in "+-."):
            continue
        if tail.endswith(match + match) or head.startswith(match + match):
            continue
        cut = ends[size-1]
        remainder = incoming[cut:]
        if remainder[:1] in EDGE_PUNCTUATION and remainder:
            previous = previous.rstrip(EDGE_PUNCTUATION + " \n\t")
        return previous + remainder, False
    return previous + separator + incoming, True
