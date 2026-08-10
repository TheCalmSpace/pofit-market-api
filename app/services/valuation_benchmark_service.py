from statistics import median
from typing import List, Optional, Dict, Any
import math

from app.repositories.stock_data_repository import StockDataRepository
from app.repositories.stock_repository import StockRepository


class ValuationBenchmarkService:
    """Compute lightweight PE benchmarks from the existing POFIT stock universe."""

    MIN_PEERS = 5

    def __init__(
        self,
        stock_repo: StockRepository,
        stock_data_repo: StockDataRepository,
    ):
        self.stock_repo = stock_repo
        self.stock_data_repo = stock_data_repo

    def build_relative_pe(
        self,
        symbol: str,
        company_pe: Optional[float],
    ) -> Optional[float]:
        """
        Return the company PE relative to an industry or sector benchmark.

        The benchmark is selected in this order:
        1. industry median PE when at least MIN_PEERS valid industry peers exist
        2. sector median PE when at least MIN_PEERS valid sector peers exist
        3. None if peer data is insufficient

        If no benchmark is available, the existing valuation fallback remains unchanged.
        """
        if not self._is_valid_pe(company_pe):
            return None

        symbol = symbol.upper()
        universe = self._load_universe()
        company = universe.get(symbol)

        if not company:
            return None

        benchmark_pe = self._benchmark_pe(company, universe)

        if benchmark_pe is None or benchmark_pe <= 0:
            return None

        return company_pe / benchmark_pe

    def _load_universe(self) -> Dict[str, Dict[str, Any]]:
        """Load the stock universe and cached valuation PE values in one batch."""
        stocks = self.stock_repo.list_all_active()
        metrics_rows = self.stock_data_repo.list_all_metrics()

        metrics_by_symbol = {
            row["symbol"].upper(): row.get("metrics_json")
            for row in metrics_rows
            if row.get("metrics_json")
        }

        universe: Dict[str, Dict[str, Any]] = {}

        for stock in stocks:
            symbol = (stock.get("symbol") or "").upper()
            if not symbol:
                continue

            metrics = metrics_by_symbol.get(symbol)
            if not metrics:
                continue

            valuation = metrics.get("valuation") or {}
            pe = valuation.get("pe")

            if not self._is_valid_pe(pe):
                continue

            universe[symbol] = {
                "symbol": symbol,
                "sector": stock.get("sector"),
                "industry": stock.get("industry"),
                "pe": float(pe),
            }

        return universe

    def _benchmark_pe(
        self,
        company: Dict[str, Any],
        universe: Dict[str, Dict[str, Any]],
    ) -> Optional[float]:
        """Choose industry-first, then sector fallback benchmark PE."""
        industry = company.get("industry")
        sector = company.get("sector")
        symbol = company.get("symbol")

        if industry:
            industry_pe = self._peer_pe_values(
                universe,
                key="industry",
                value=industry,
                exclude_symbol=symbol,
            )
            if len(industry_pe) >= self.MIN_PEERS:
                return self._median(industry_pe)

        if sector:
            sector_pe = self._peer_pe_values(
                universe,
                key="sector",
                value=sector,
                exclude_symbol=symbol,
            )
            if len(sector_pe) >= self.MIN_PEERS:
                return self._median(sector_pe)

        return None

    def _peer_pe_values(
        self,
        universe: Dict[str, Dict[str, Any]],
        key: str,
        value: Any,
        exclude_symbol: Optional[str] = None,
    ) -> List[float]:
        peers: List[float] = []

        for symbol, row in universe.items():
            if exclude_symbol and symbol == exclude_symbol:
                continue
            if row.get(key) != value:
                continue
            peers.append(row["pe"])

        return peers

    @staticmethod
    def _median(values: List[float]) -> float:
        return float(median(values))

    @staticmethod
    def _is_valid_pe(value: Optional[float]) -> bool:
        return (
            isinstance(value, (int, float))
            and value > 0
            and math.isfinite(value)
        )
