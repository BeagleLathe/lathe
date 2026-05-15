"""String normalization + fuzzy substring search.

normalize(s) returns (normalized_text, offset_map) where offset_map[i] is the
index in the *original* string corresponding to character i in the normalized
string. This lets a caller find a match in normalized space and map it back to
the original byte/char offsets so an edit can be applied to the unmodified file.

fuzzy_find returns the best window in `haystack` matching `needle`, or the
string "ambiguous" if there are multiple non-overlapping near-best matches
within 2 score points of each other.
"""

from __future__ import annotations

from typing import Union

from rapidfuzz import fuzz


def normalize(s: str) -> tuple[str, list[int]]:
    out_chars: list[str] = []
    offsets: list[int] = []
    line_chars: list[str] = []
    line_offsets: list[int] = []

    def flush_line() -> None:
        while line_chars and line_chars[-1] in (" ", "\t"):
            line_chars.pop()
            line_offsets.pop()
        out_chars.extend(line_chars)
        offsets.extend(line_offsets)
        line_chars.clear()
        line_offsets.clear()

    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\r":
            flush_line()
            out_chars.append("\n")
            offsets.append(i)
            if i + 1 < n and s[i + 1] == "\n":
                i += 2
            else:
                i += 1
            continue
        if c == "\n":
            flush_line()
            out_chars.append("\n")
            offsets.append(i)
            i += 1
            continue
        if c in (" ", "\t"):
            start = i
            while i < n and s[i] in (" ", "\t"):
                i += 1
            line_chars.append(" ")
            line_offsets.append(start)
            continue
        if c in ("‘", "’"):
            line_chars.append("'")
            line_offsets.append(i)
            i += 1
            continue
        if c in ("“", "”"):
            line_chars.append('"')
            line_offsets.append(i)
            i += 1
            continue
        if c in ("—", "–"):
            line_chars.append("-")
            line_offsets.append(i)
            i += 1
            continue
        if c == "…":
            line_chars.append(".")
            line_offsets.append(i)
            line_chars.append(".")
            line_offsets.append(i)
            line_chars.append(".")
            line_offsets.append(i)
            i += 1
            continue
        line_chars.append(c)
        line_offsets.append(i)
        i += 1

    flush_line()
    return "".join(out_chars), offsets


def fuzzy_find(
    haystack: str,
    needle: str,
    min_score: int = 90,
) -> Union[tuple[int, int, int], str, None]:
    nlen = len(needle)
    hlen = len(haystack)
    if nlen == 0 or nlen > hlen:
        return None

    candidates: list[tuple[int, int]] = []
    for start in range(0, hlen - nlen + 1):
        window = haystack[start : start + nlen]
        score = int(fuzz.ratio(needle, window))
        if score >= min_score:
            candidates.append((score, start))

    if not candidates:
        return None

    candidates.sort(key=lambda x: -x[0])
    best_score = candidates[0][0]

    near_best = [(sc, st) for sc, st in candidates if best_score - sc <= 2]

    non_overlap: list[tuple[int, int]] = []
    for sc, st in near_best:
        en = st + nlen
        overlap = False
        for _, st2 in non_overlap:
            en2 = st2 + nlen
            if not (en <= st2 or en2 <= st):
                overlap = True
                break
        if not overlap:
            non_overlap.append((sc, st))

    if len(non_overlap) > 1:
        return "ambiguous"

    best_start = candidates[0][1]
    return (best_start, best_start + nlen, best_score)
