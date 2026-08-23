#!/usr/bin/env python3
"""
Representative universe test for pofit-market-api.
Tests stocks across multiple categories to verify fixes are generic.
"""

import json
import os
import sys
import time
from datetime import datetime

import requests

BASE_URL = os.getenv("POFIT_MARKET_API_BASE_URL", "http://127.0.0.1:8001")

CATEGORIES = {
    "Large-cap IT": ["TCS", "INFY", "WIPRO"],
    "Large-cap Bank": ["HDFCBANK", "ICICIBANK", "SBIN"],
    "Large-cap FMCG": ["HINDUNILVR", "ITC", "NESTLEIND"],
    "Large-cap Energy": ["RELIANCE", "ONGC", "BPCL"],
    "Large-cap Pharma": ["SUNPHARMA", "CIPLA", "DRREDDY"],
    "Mid-cap Finance": ["SBICARD", "MUTHOOTFIN", "BAJFINANCE"],
    "Mid-cap IT": ["LTTS", "PERSISTENT", "COFORGE"],
    "Small-cap": ["RAIN", "JMFINANCIL", "SALASAR"],
    "Newly listed": ["NTPC", "TATASTEEL"],  # may have limited history
    "Financial institution": ["AXISBANK", "KOTAKBANK", "INDUSINDBK"],
}

TIMEFRAMES = ["5d", "1mo", "3mo", "1y"]


def check_endpoint(symbol, endpoint, params=None):
    """Check a single endpoint and return status and data."""
    url = f"{BASE_URL}/{endpoint}/{symbol}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    
    try:
        start = time.time()
        resp = requests.get(url, timeout=30)
        elapsed = time.time() - start
        
        if resp.status_code == 200:
            data = resp.json()
            data_status = data.get("data_status", "available")
            return {
                "status": "success" if data_status == "available" else data_status,
                "http_code": resp.status_code,
                "elapsed": round(elapsed, 2),
                "data_status": data_status,
                "unavailable_reason": data.get("unavailable_reason"),
                "sample": _sample(data),
            }
        else:
            return {
                "status": "http_error",
                "http_code": resp.status_code,
                "elapsed": round(elapsed, 2),
                "body": resp.text[:200],
            }
    except requests.exceptions.Timeout:
        return {"status": "timeout", "elapsed": 30}
    except Exception as e:
        return {"status": "exception", "error": str(e)}


def _sample(data, max_keys=10):
    """Extract a small sample from response for reporting."""
    if isinstance(data, dict):
        keys = list(data.keys())[:max_keys]
        return {k: data[k] for k in keys if k in data}
    elif isinstance(data, list):
        return f"list[{len(data)}]"
    return str(data)[:200]


def check_symbol(symbol):
    """Run all checks for a single symbol."""
    results = {"symbol": symbol, "tests": {}}
    
    # Quote
    results["tests"]["quote"] = check_endpoint(symbol, "quote")
    
    # Historical for multiple timeframes
    hist_results = {}
    for tf in TIMEFRAMES:
        r = check_endpoint(symbol, "historical", {"period": tf, "interval": "1d"})
        hist_results[tf] = r
    results["tests"]["historical"] = hist_results
    
    # Financials
    results["tests"]["financials"] = check_endpoint(symbol, "financials")
    
    # Financial history
    results["tests"]["financial_history"] = check_endpoint(symbol, "financial-history")
    
    # Metrics
    results["tests"]["metrics"] = check_endpoint(symbol, "metrics")
    
    # Score
    results["tests"]["score"] = check_endpoint(symbol, "score")
    
    return results


def main():
    print("=" * 80)
    print("POFIT-MARKET-API UNIVERSE RELIABILITY TEST")
    print(f"Target: {BASE_URL}")
    print(f"Time: {datetime.utcnow().isoformat()}Z")
    print("=" * 80)
    
    all_results = {}
    category_summary = {}
    
    for category, symbols in CATEGORIES.items():
        print(f"\n--- {category} ---")
        category_summary[category] = {"total": 0, "success": 0, "partial": 0, "unavailable": 0, "error": 0}
        
        for symbol in symbols:
            print(f"  Testing {symbol}...", end="", flush=True)
            result = check_symbol(symbol)
            all_results[symbol] = result
            
            # Summarize
            tests = result["tests"]
            quote_ok = tests["quote"].get("status") == "success"
            hist_ok = any(t.get("status") == "success" for t in tests["historical"].values())
            fin_ok = tests["financials"].get("status") == "success"
            fh_ok = tests["financial_history"].get("status") == "success"
            metrics_status = tests["metrics"].get("data_status", "unknown")
            score_status = tests["score"].get("data_status", "unknown")
            
            if quote_ok and hist_ok and fin_ok and fh_ok:
                category_summary[category]["success"] += 1
                print(" FULL DATA")
            elif quote_ok and (fin_ok or fh_ok):
                category_summary[category]["partial"] += 1
                print(" PARTIAL")
            elif quote_ok:
                category_summary[category]["unavailable"] += 1
                print(" UNAVAILABLE")
            else:
                category_summary[category]["error"] += 1
                print(f" ERROR ({tests['quote'].get('status')})")
            
            category_summary[category]["total"] += 1
            
            # Small delay to avoid rate limiting
            time.sleep(0.5)
    
    # Print summary
    print("\n" + "=" * 80)
    print("CATEGORY SUMMARY")
    print("=" * 80)
    for cat, stats in category_summary.items():
        print(f"{cat:25} total={stats['total']} success={stats['success']} partial={stats['partial']} unavailable={stats['unavailable']} error={stats['error']}")
    
    # Save detailed results
    with open("universe_test_results.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nDetailed results saved to universe_test_results.json")
    
    # Identify systemic issues
    print("\n" + "=" * 80)
    print("SYSTEMIC ISSUES")
    print("=" * 80)
    
    quote_failures = [s for s, r in all_results.items() if r["tests"]["quote"].get("status") != "success"]
    hist_failures = {s: [tf for tf, t in r["tests"]["historical"].items() if t.get("status") != "success"] 
                     for s, r in all_results.items()}
    fin_failures = [s for s, r in all_results.items() if r["tests"]["financials"].get("status") != "success"]
    fh_failures = [s for s, r in all_results.items() if r["tests"]["financial_history"].get("status") != "success"]
    metrics_failures = [s for s, r in all_results.items() if r["tests"]["metrics"].get("data_status") not in ("available", "insufficient_data")]
    
    if quote_failures:
        print(f"Quote failures: {', '.join(quote_failures)}")
    if any(hist_failures.values()):
        print(f"Historical failures: {hist_failures}")
    if fin_failures:
        print(f"Financials failures: {', '.join(fin_failures)}")
    if fh_failures:
        print(f"Financial history failures: {', '.join(fh_failures)}")
    if metrics_failures:
        print(f"Metrics failures: {', '.join(metrics_failures)}")
    
    if not quote_failures and not fin_failures and not metrics_failures:
        print("No systemic failures detected across tested universe.")


if __name__ == "__main__":
    main()
