import math
import traceback
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set

import yfinance as yf

from app.models import (
    FinancialHistoryAnnualItem,
    FinancialHistoryResponse,
    FinancialsResponse,
    HistoricalPrice,
    HistoricalResponse,
    QuoteResponse,
    SearchResponse,
    SearchResult,
)


class MarketDataError(Exception):
    """Base exception for market data service failures."""


class SymbolNotFoundError(MarketDataError):
    def __init__(self, symbol: str) -> None:
        super().__init__(f"Symbol '{symbol}' was not found.")


class InvalidMarketDataRequestError(MarketDataError):
    pass


class MarketDataUnavailableError(MarketDataError):
    pass


class YahooService:
    _VALID_PERIODS = {
        "1d",
        "5d",
        "1mo",
        "3mo",
        "6mo",
        "1y",
        "2y",
        "5y",
        "10y",
        "ytd",
        "max",
    }
    _VALID_INTERVALS = {
        "1m",
        "2m",
        "5m",
        "15m",
        "30m",
        "60m",
        "90m",
        "1h",
        "1d",
        "5d",
        "1wk",
        "1mo",
        "3mo",
    }
    _REVENUE_ROWS = ("Total Revenue", "TotalRevenue")
    _GROSS_PROFIT_ROWS = ("Gross Profit", "GrossProfit")
    _EBITDA_ROWS = ("EBITDA", "Normalized EBITDA")
    _OPERATING_INCOME_ROWS = ("Operating Income", "OperatingIncome")
    _NET_INCOME_ROWS = ("Net Income", "Net Income Common Stockholders", "NetIncome")
    _OPERATING_CASH_FLOW_ROWS = (
        "Operating Cash Flow",
        "Total Cash From Operating Activities",
        "Cash Flow From Continuing Operating Activities",
    )
    _FREE_CASH_FLOW_ROWS = ("Free Cash Flow", "FreeCashFlow")
    _CAPITAL_EXPENDITURE_ROWS = (
        "Capital Expenditure",
        "Capital Expenditures",
        "CapitalExpenditure",
    )
    _CASH_ROWS = (
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "Cash And Short Term Investments",
    )
    _TOTAL_DEBT_ROWS = ("Total Debt", "TotalDebt")
    _TOTAL_ASSETS_ROWS = ("Total Assets", "TotalAssets")
    _TOTAL_LIABILITIES_ROWS = (
        "Total Liabilities Net Minority Interest",
        "Total Liab",
        "Total Liabilities",
    )
    _SHAREHOLDERS_EQUITY_ROWS = (
        "Stockholders Equity",
        "Total Equity Gross Minority Interest",
        "Total Stockholder Equity",
    )
    _EPS_ROWS = ("Diluted EPS", "Basic EPS", "DilutedEPS", "BasicEPS")

    def get_quote(self, symbol: str) -> QuoteResponse:
        normalized_symbol = self._normalize_symbol(symbol)
        ticker = yf.Ticker(normalized_symbol)

        info = self._get_info(ticker=ticker, symbol=normalized_symbol)
        fast_info = self._get_fast_info(ticker=ticker)

        current_price = self._first_number(
            info.get("currentPrice"),
            info.get("regularMarketPrice"),
            fast_info.get("last_price"),
            fast_info.get("lastPrice"),
        )

        if current_price is None:
            raise SymbolNotFoundError(normalized_symbol)

        return QuoteResponse(
            symbol=normalized_symbol,
            company_name=self._first_string(
                info.get("longName"),
                info.get("shortName"),
                info.get("displayName"),
                normalized_symbol,
            )
            or normalized_symbol,
            current_price=current_price,
            currency=self._first_string(info.get("currency"), fast_info.get("currency")),
            exchange=self._first_string(
                info.get("exchange"),
                info.get("fullExchangeName"),
                fast_info.get("exchange"),
            ),
            market_cap=self._first_int(info.get("marketCap"), fast_info.get("market_cap")),
            previous_close=self._first_number(
                info.get("previousClose"),
                info.get("regularMarketPreviousClose"),
                fast_info.get("previous_close"),
            ),
        )

    def search_symbols(self, query: str, max_results: int = 8) -> SearchResponse:
        clean_query = query.strip()
        if not clean_query:
            raise InvalidMarketDataRequestError("Search query cannot be empty.")
        if max_results < 1:
            raise InvalidMarketDataRequestError("max_results must be greater than zero.")

        try:
            search_result = yf.Search(
                clean_query,
                max_results=max_results,
                news_count=0,
                lists_count=0,
                include_research=False,
                include_cultural_assets=False,
            )
        except Exception as exc:
            raise MarketDataUnavailableError("Unable to search Yahoo Finance right now.") from exc

        results: List[SearchResult] = []
        for item in search_result.quotes:
            if not isinstance(item, dict):
                continue

            result_symbol = self._first_string(item.get("symbol"))
            if result_symbol is None:
                continue

            results.append(
                SearchResult(
                    symbol=result_symbol,
                    name=self._first_string(
                        item.get("longname"),
                        item.get("shortname"),
                        item.get("name"),
                        result_symbol,
                    )
                    or result_symbol,
                    exchange=self._first_string(item.get("exchange")),
                    quote_type=self._first_string(item.get("quoteType")),
                    currency=self._first_string(item.get("currency")),
                )
            )

        return SearchResponse(query=clean_query, results=results)

    def get_historical_prices(
        self,
        symbol: str,
        period: str = "1mo",
        interval: str = "1d",
    ) -> HistoricalResponse:
        normalized_symbol = self._normalize_symbol(symbol)
        normalized_period = self._normalize_period(period)
        normalized_interval = self._normalize_interval(interval)

        ticker = yf.Ticker(normalized_symbol)

        try:
            history = ticker.history(
                period=normalized_period,
                interval=normalized_interval,
                auto_adjust=False,
            )
        except Exception as exc:
            if self._looks_like_missing_symbol_error(exc):
                raise SymbolNotFoundError(normalized_symbol) from exc
            raise MarketDataUnavailableError(
                f"Unable to fetch historical data for '{normalized_symbol}'."
            ) from exc

        if history is None or history.empty:
            raise SymbolNotFoundError(normalized_symbol)

        prices: List[HistoricalPrice] = []
        for index, row in history.iterrows():
            prices.append(
                HistoricalPrice(
                    date=self._to_date(index),
                    open=self._to_float(row.get("Open")),
                    high=self._to_float(row.get("High")),
                    low=self._to_float(row.get("Low")),
                    close=self._to_float(row.get("Close")),
                    adj_close=self._to_float(row.get("Adj Close")),
                    volume=self._to_int(row.get("Volume")),
                )
            )

        return HistoricalResponse(
            symbol=normalized_symbol,
            period=normalized_period,
            interval=normalized_interval,
            prices=prices,
        )

    def get_financials(self, symbol: str) -> FinancialsResponse:
        normalized_symbol = self._normalize_symbol(symbol)
        ticker = yf.Ticker(normalized_symbol)

        info = self._get_info(
    ticker=ticker,
    symbol=normalized_symbol,
)
        if self._is_invalid_symbol_info(info):
            raise SymbolNotFoundError(normalized_symbol)

        fast_info = self._get_fast_info(ticker=ticker)
        income_statement = self._get_statement(ticker=ticker, attribute_name="financials")
        cash_flow = self._get_statement(ticker=ticker, attribute_name="cashflow")
        balance_sheet = self._get_statement(ticker=ticker, attribute_name="balance_sheet")

        return FinancialsResponse(
            symbol=normalized_symbol,
            company_name=self._first_string(
                info.get("longName"),
                info.get("shortName"),
                info.get("displayName"),
            ),
            sector=self._first_string(info.get("sector")),
            industry=self._first_string(info.get("industry")),
            country=self._first_string(info.get("country")),
            currency=self._first_string(
                info.get("currency"),
                info.get("financialCurrency"),
                fast_info.get("currency"),
            ),
            market_cap=self._first_int(info.get("marketCap"), fast_info.get("market_cap")),
            enterprise_value=self._first_int(info.get("enterpriseValue")),
            shares_outstanding=self._first_int(
                info.get("sharesOutstanding"),
                info.get("impliedSharesOutstanding"),
            ),
            employees=self._first_int(info.get("fullTimeEmployees")),
            revenue_ttm=self._first_number(
                info.get("totalRevenue"),
                self._get_statement_value(income_statement, *self._REVENUE_ROWS),
            ),
            gross_profit=self._first_number(
                info.get("grossProfits"),
                self._get_statement_value(income_statement, *self._GROSS_PROFIT_ROWS),
            ),
            ebitda=self._first_number(
                info.get("ebitda"),
                self._get_statement_value(income_statement, *self._EBITDA_ROWS),
            ),
            operating_income=self._first_number(
                self._get_statement_value(income_statement, *self._OPERATING_INCOME_ROWS)
            ),
            net_income=self._first_number(
                info.get("netIncomeToCommon"),
                self._get_statement_value(income_statement, *self._NET_INCOME_ROWS),
            ),
            operating_cash_flow=self._first_number(
                info.get("operatingCashflow"),
                self._get_statement_value(cash_flow, *self._OPERATING_CASH_FLOW_ROWS),
            ),
            free_cash_flow=self._first_number(
                info.get("freeCashflow"),
                self._get_statement_value(cash_flow, *self._FREE_CASH_FLOW_ROWS),
            ),
            capital_expenditure=self._first_number(
                info.get("capitalExpenditures"),
                self._get_statement_value(cash_flow, *self._CAPITAL_EXPENDITURE_ROWS),
            ),
            cash=self._first_number(
                info.get("totalCash"),
                self._get_statement_value(balance_sheet, *self._CASH_ROWS),
            ),
            total_debt=self._first_number(
                info.get("totalDebt"),
                self._get_statement_value(balance_sheet, *self._TOTAL_DEBT_ROWS),
            ),
            total_assets=self._first_number(
                self._get_statement_value(balance_sheet, *self._TOTAL_ASSETS_ROWS)
            ),
            total_liabilities=self._first_number(
                self._get_statement_value(balance_sheet, *self._TOTAL_LIABILITIES_ROWS)
            ),
            total_equity=self._first_number(
                self._get_statement_value(balance_sheet, *self._SHAREHOLDERS_EQUITY_ROWS)
            ),
            book_value_per_share=self._first_number(info.get("bookValue")),
            eps=self._first_number(info.get("trailingEps"), info.get("forwardEps")),
            trailing_pe=self._first_number(info.get("trailingPE")),
            forward_pe=self._first_number(info.get("forwardPE")),
            peg_ratio=self._first_number(info.get("pegRatio"), info.get("trailingPegRatio")),
            price_to_book=self._first_number(info.get("priceToBook")),
            return_on_equity=self._first_number(info.get("returnOnEquity")),
            return_on_assets=self._first_number(info.get("returnOnAssets")),
            current_ratio=self._first_number(info.get("currentRatio")),
            quick_ratio=self._first_number(info.get("quickRatio")),
            debt_to_equity=self._first_number(info.get("debtToEquity")),
            profit_margin=self._first_number(info.get("profitMargins")),
            operating_margin=self._first_number(info.get("operatingMargins")),
            dividend_yield=self._first_number(info.get("dividendYield")),
        )

    def get_financial_history(self, symbol: str) -> FinancialHistoryResponse:
        normalized_symbol = self._normalize_symbol(symbol)
        ticker = yf.Ticker(normalized_symbol)

        info = self._get_info(ticker=ticker, symbol=normalized_symbol)
        if self._is_invalid_symbol_info(info):
            raise SymbolNotFoundError(normalized_symbol)

        fast_info = self._get_fast_info(ticker=ticker)
        income_statement = self._get_statement(
            ticker=ticker,
            attribute_name="financials",
            symbol=normalized_symbol,
            raise_on_error=True,
        )
        cash_flow = self._get_statement(
            ticker=ticker,
            attribute_name="cashflow",
            symbol=normalized_symbol,
            raise_on_error=True,
        )
        balance_sheet = self._get_statement(
            ticker=ticker,
            attribute_name="balance_sheet",
            symbol=normalized_symbol,
            raise_on_error=True,
        )

        annual_items: List[FinancialHistoryAnnualItem] = []
        for year in self._get_statement_years(income_statement, cash_flow, balance_sheet):
            annual_items.append(
                FinancialHistoryAnnualItem(
                    year=year,
                    revenue=self._get_statement_value_by_year(
                        income_statement,
                        year,
                        *self._REVENUE_ROWS,
                    ),
                    gross_profit=self._get_statement_value_by_year(
                        income_statement,
                        year,
                        *self._GROSS_PROFIT_ROWS,
                    ),
                    operating_income=self._get_statement_value_by_year(
                        income_statement,
                        year,
                        *self._OPERATING_INCOME_ROWS,
                    ),
                    net_income=self._get_statement_value_by_year(
                        income_statement,
                        year,
                        *self._NET_INCOME_ROWS,
                    ),
                    operating_cash_flow=self._get_statement_value_by_year(
                        cash_flow,
                        year,
                        *self._OPERATING_CASH_FLOW_ROWS,
                    ),
                    free_cash_flow=self._get_statement_value_by_year(
                        cash_flow,
                        year,
                        *self._FREE_CASH_FLOW_ROWS,
                    ),
                    capital_expenditure=self._get_statement_value_by_year(
                        cash_flow,
                        year,
                        *self._CAPITAL_EXPENDITURE_ROWS,
                    ),
                    cash=self._get_statement_value_by_year(
                        balance_sheet,
                        year,
                        *self._CASH_ROWS,
                    ),
                    total_debt=self._get_statement_value_by_year(
                        balance_sheet,
                        year,
                        *self._TOTAL_DEBT_ROWS,
                    ),
                    total_assets=self._get_statement_value_by_year(
                        balance_sheet,
                        year,
                        *self._TOTAL_ASSETS_ROWS,
                    ),
                    total_liabilities=self._get_statement_value_by_year(
                        balance_sheet,
                        year,
                        *self._TOTAL_LIABILITIES_ROWS,
                    ),
                    shareholders_equity=self._get_statement_value_by_year(
                        balance_sheet,
                        year,
                        *self._SHAREHOLDERS_EQUITY_ROWS,
                    ),
                    eps=self._get_statement_value_by_year(
                        income_statement,
                        year,
                        *self._EPS_ROWS,
                    ),
                )
            )

        return FinancialHistoryResponse(
            symbol=normalized_symbol,
            currency=self._first_string(
                info.get("currency"),
                info.get("financialCurrency"),
                fast_info.get("currency"),
            ),
            annual=annual_items,
        )

    def _get_info(self, ticker: Any, symbol: str) -> Dict[str, Any]:
        try:
            info = ticker.info
        except Exception as exc:
            if self._looks_like_missing_symbol_error(exc):
                raise SymbolNotFoundError(symbol) from exc

            print("=" * 80)
            print(f"ERROR FETCHING SYMBOL: {symbol}")
            traceback.print_exception(type(exc), exc, exc.__traceback__)
            print("=" * 80)

            raise MarketDataUnavailableError(
                f"Unable to fetch quote data for '{symbol}'. "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        if not isinstance(info, dict):
            return {}

        return info

    def _get_statement(
        self,
        ticker: Any,
        attribute_name: str,
        symbol: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> Any:
        try:
            return getattr(ticker, attribute_name)
        except Exception as exc:
            if raise_on_error:
                if symbol is not None and self._looks_like_missing_symbol_error(exc):
                    raise SymbolNotFoundError(symbol) from exc

                statement_name = attribute_name.replace("_", " ")
                if symbol is None:
                    raise MarketDataUnavailableError(
                        f"Unable to fetch Yahoo Finance {statement_name}."
                    ) from exc
                raise MarketDataUnavailableError(
                    f"Unable to fetch Yahoo Finance {statement_name} for '{symbol}'."
                ) from exc

            return None

    def _get_statement_value(self, statement: Any, *row_names: str) -> Optional[float]:
        return self._get_statement_value_for_columns(
            statement=statement,
            columns=self._get_statement_columns(statement),
            row_names=row_names,
        )

    def _get_statement_value_by_year(
        self,
        statement: Any,
        year: int,
        *row_names: str,
    ) -> Optional[float]:
        columns = [
            column
            for column in self._get_statement_columns(statement)
            if self._column_to_year(column) == year
        ]
        return self._get_statement_value_for_columns(
            statement=statement,
            columns=columns,
            row_names=row_names,
        )

    def _get_statement_value_for_columns(
        self,
        statement: Any,
        columns: List[Any],
        row_names: Sequence[str],
    ) -> Optional[float]:
        if statement is None or getattr(statement, "empty", True):
            return None

        for row_name in row_names:
            if not self._statement_has_row(statement=statement, row_name=row_name):
                continue

            for column in columns:
                try:
                    raw_value = statement.loc[row_name, column]
                except Exception:
                    continue

                value = self._first_number(*self._series_to_values(raw_value))
                if value is not None:
                    return value

        return None

    def _get_statement_years(self, *statements: Any) -> List[int]:
        years: Set[int] = set()

        for statement in statements:
            for column in self._get_statement_columns(statement):
                year = self._column_to_year(column)
                if year is not None:
                    years.add(year)

        return sorted(years, reverse=True)

    @staticmethod
    def _get_statement_columns(statement: Any) -> List[Any]:
        if statement is None or getattr(statement, "empty", True):
            return []

        try:
            return list(statement.columns)
        except Exception:
            return []

    @staticmethod
    def _statement_has_row(statement: Any, row_name: str) -> bool:
        try:
            return row_name in statement.index
        except Exception:
            return False

    def _get_fast_info(self, ticker: Any) -> Dict[str, Any]:
        keys = (
            "currency",
            "exchange",
            "last_price",
            "lastPrice",
            "market_cap",
            "previous_close",
        )
        fast_info: Dict[str, Any] = {}

        try:
            raw_fast_info = ticker.fast_info
        except Exception:
            return fast_info

        for key in keys:
            try:
                fast_info[key] = raw_fast_info.get(key)
            except Exception:
                continue

        return fast_info

    def _normalize_symbol(self, symbol: str) -> str:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise SymbolNotFoundError(symbol)
        return normalized_symbol

    def _is_invalid_symbol_info(self, info: Dict[str, Any]) -> bool:
        if not info:
            return True

        quote_type = self._first_string(info.get("quoteType"))
        if quote_type is not None and quote_type.upper() == "NONE":
            return True

        identity_fields = (
            info.get("longName"),
            info.get("shortName"),
            info.get("displayName"),
            info.get("sector"),
            info.get("industry"),
            info.get("currency"),
            info.get("financialCurrency"),
            info.get("marketCap"),
            info.get("regularMarketPrice"),
            info.get("currentPrice"),
        )
        return all(self._is_missing(value) for value in identity_fields)

    def _normalize_period(self, period: str) -> str:
        normalized_period = period.strip().lower()
        if normalized_period not in self._VALID_PERIODS:
            raise InvalidMarketDataRequestError(
                f"Unsupported period '{period}'. Supported values: {', '.join(sorted(self._VALID_PERIODS))}."
            )
        return normalized_period

    def _normalize_interval(self, interval: str) -> str:
        normalized_interval = interval.strip().lower()
        if normalized_interval not in self._VALID_INTERVALS:
            raise InvalidMarketDataRequestError(
                f"Unsupported interval '{interval}'. Supported values: {', '.join(sorted(self._VALID_INTERVALS))}."
            )
        return normalized_interval

    @classmethod
    def _first_string(cls, *values: Any) -> Optional[str]:
        for value in values:
            if cls._is_missing(value):
                continue
            return str(value).strip()
        return None

    @classmethod
    def _first_number(cls, *values: Any) -> Optional[float]:
        for value in values:
            number = cls._to_float(value)
            if number is not None:
                return number
        return None

    @classmethod
    def _first_int(cls, *values: Any) -> Optional[int]:
        for value in values:
            number = cls._to_int(value)
            if number is not None:
                return number
        return None

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if YahooService._is_missing(value):
            return None

        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

        if not math.isfinite(number):
            return None
        return number

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        number = YahooService._to_float(value)
        if number is None:
            return None
        return int(number)

    @staticmethod
    def _to_date(value: Any) -> date:
        if hasattr(value, "date"):
            return value.date()
        return date.fromisoformat(str(value)[:10])

    @staticmethod
    def _column_to_year(value: Any) -> Optional[int]:
        if hasattr(value, "year"):
            try:
                return int(value.year)
            except (TypeError, ValueError):
                return None

        string_value = str(value).strip()
        if len(string_value) < 4:
            return None

        try:
            return int(string_value[:4])
        except ValueError:
            return None

    @staticmethod
    def _series_to_values(value: Any) -> List[Any]:
        if hasattr(value, "dropna"):
            try:
                value = value.dropna()
            except Exception:
                pass

        if hasattr(value, "tolist"):
            try:
                listed_value = value.tolist()
                if isinstance(listed_value, list):
                    return listed_value
                return [listed_value]
            except Exception:
                return [value]

        return [value]

    @staticmethod
    def _is_missing(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == "" or value.strip().lower() in {"nan", "none", "null"}

        try:
            return bool(math.isnan(value))
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _looks_like_missing_symbol_error(exc: Exception) -> bool:
        message = str(exc).lower()
        missing_symbol_markers = (
            "404",
            "not found",
            "no price data found",
            "possibly delisted",
            "symbol may be delisted",
            "quote not found",
        )
        return any(marker in message for marker in missing_symbol_markers)
