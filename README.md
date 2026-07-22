# POFIT Market API

Production-ready FastAPI backend for POFIT market data.

## Project structure

```text
pofit-market-api/
app/
    __init__.py
    main.py
    routers/
        __init__.py
        quote.py
        search.py
        historical.py
    services/
        __init__.py
        yahoo_service.py
    models/
        __init__.py
        quote.py
requirements.txt
README.md
```

## Setup

```powershell
cd pofit-market-api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`.

## Endpoints

```text
GET /                         Health check
GET /quote/{symbol}           Current quote data
GET /search?q={query}         Symbol search
GET /historical/{symbol}      Historical OHLCV data
```

Historical data supports optional query parameters:

```text
GET /historical/AAPL?period=1mo&interval=1d
```

## Example responses

```json
{
  "status": "POFIT Market API is running",
  "version": "1.0.0"
}
```

```json
{
  "symbol": "AAPL",
  "company_name": "Apple Inc.",
  "current_price": 210.0,
  "currency": "USD",
  "exchange": "NMS",
  "market_cap": 3150000000000,
  "previous_close": 209.5
}
```

## Error handling

Invalid symbols return `404`.
Invalid request parameters return `400`.
Yahoo Finance availability issues return `503`.

## Extension notes

Business logic lives in `app/services/yahoo_service.py`, while routers only translate HTTP requests into service calls. Future features such as financials, balance sheet, cash flow, income statement, and GARP scoring can be added by creating new service methods, Pydantic models, and routers without changing the existing route contracts.
