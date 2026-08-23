from typing import Optional


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
