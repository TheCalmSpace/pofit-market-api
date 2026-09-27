from typing import Iterable, List, Sequence


# PostgREST receives an `or=(symbol.eq.A,symbol.eq.B,...)` filter as part of the
# request URL, not in the request body. A full POFIT universe (2.4k NSE symbols,
# 8.5k US symbols) produces a filter string of roughly 35KB-127KB, which the
# Supabase/Kong gateway rejects with a bare HTTP 400 before PostgREST can parse
# it. Requests must therefore be split into batches small enough that the
# generated URL stays within gateway limits.
#
# 200 symbols yields a filter of about 3KB, which is comfortably accepted.
SYMBOL_BATCH_SIZE = 200


def batched(values: Sequence[str], size: int = SYMBOL_BATCH_SIZE) -> Iterable[List[str]]:
    """Yield consecutive slices of `values`, each at most `size` long."""
    for start in range(0, len(values), size):
        yield list(values[start:start + size])


def normalized_unique_symbols(symbols: Sequence[str]) -> List[str]:
    """Upper-case, trim and de-duplicate symbols, preserving a stable order."""
    seen = set()
    normalized: List[str] = []

    for symbol in symbols:
        if not symbol:
            continue
        cleaned = str(symbol).strip().upper()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)

    return normalized
