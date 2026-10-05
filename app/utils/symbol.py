from typing import Dict, FrozenSet, Optional


# Canonical market -> benchmark index mapping.
#
# This lives in a leaf utility so that both the quote refresh that populates the
# benchmark cache and the performance service that reads it resolve the same
# symbol. PerformanceService imports this mapping only; it must never gain a
# dependency on a market-data provider to fetch a benchmark.
#
# These are Yahoo index tickers. They are deliberately NOT rows in the `stocks`
# table: a stock row would enrol the index in the normal universe, where the
# bounded ingestion job would run a full refresh_stock (quote + financials +
# 5y history) against it every 4 hours forever, because an index has no
# financial history to satisfy the cache-freshness test.
MARKET_BENCHMARKS: Dict[str, str] = {
    "IN": "^NSEI",
    "US": "^IXIC",
}

# Symbols accepted by the benchmark quote refresh. Any other symbol is
# rejected, so this path can never be used to write a partial quote-only row
# for a real stock and bypass the normal ingestion pipeline.
BENCHMARK_SYMBOLS: FrozenSet[str] = frozenset(MARKET_BENCHMARKS.values())


def resolve_yahoo_symbol(symbol: str, exchange: Optional[str] = None) -> str:
    """
    Map a canonical symbol + exchange to Yahoo Finance ticker format.

    Rules:
    - If the symbol already contains a dot, return as-is.
    - NSE → append .NS
    - BSE → append .BO
    - Otherwise return the symbol unchanged.
    """
    if "." in symbol:
        return symbol

    exchange = (exchange or "").upper()
    if exchange == "NSE":
        return f"{symbol}.NS"
    if exchange == "BSE":
        return f"{symbol}.BO"

    return symbol
