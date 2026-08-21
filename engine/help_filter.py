"""engine/help_filter.py -- ``help <topic> <query>`` line/block filter.

Long topic pages (especially ``help gm``) are hard to scan in telnet.
When lookup is ``help gm criminal``, cmd_help keeps the ``gm`` page and
this helper returns only the blocks that mention the query so staff are
not dumped the full catalog.

No game imports -- works for any HELP_TOPICS page.
"""


def filter_topic_lines(body, query, *, max_lines=50, max_whole_block=8):
    """Return matching blocks from a help topic, or None if nothing hits.

    Splits the page on blank lines (the same paragraph/cluster grouping
    authors already use) and keeps a block when any line contains the
    query (case-insensitive). Long blocks (How-you-play walls) shrink to
    the matching lines plus one neighbor so ``help gm criminal`` does not
    dump the whole hub index. Caps total length so a very common word
    cannot reprint the full page.
    """
    q = (query or "").strip().lower()
    if not q or not body:
        return None
    # Keep original newlines; strip only the wrapping that cmd_help also
    # strips so a filtered page matches the full page's inner text.
    raw_lines = str(body).strip("\n").split("\n")
    blocks = []
    current = []
    for line in raw_lines:
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)

    matched = []
    for block in blocks:
        if any(q in line.lower() for line in block):
            matched.append(_shrink_block(block, q, max_whole_block))
    if not matched:
        return None

    out = []
    for i, block in enumerate(matched):
        if i:
            out.append("")
        out.extend(block)
        if len(out) >= max_lines:
            out = out[:max_lines]
            out.append("")
            out.append("(truncated -- type the topic alone for the full page)")
            break
    return out


def _shrink_block(block, query, max_whole_block):
    """Keep a short block whole; clip a long one to matching lines."""
    if len(block) <= max_whole_block:
        return list(block)
    keep = set()
    # Unindented first line is usually the section header -- keep it so
    # the clipped cluster still says which heading the hits came from.
    if block and not block[0].startswith((" ", "\t")):
        keep.add(0)
    for i, line in enumerate(block):
        if query in line.lower():
            for j in range(max(0, i - 1), min(len(block), i + 2)):
                keep.add(j)
    return [block[i] for i in sorted(keep)]
