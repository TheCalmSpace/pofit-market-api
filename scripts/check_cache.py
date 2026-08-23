import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

for sym in ["RELIANCE", "TCS", "INFY", "HDFCBANK"]:
    r = sb.table("stock_data").select("metrics_json, score_json").eq("symbol", sym).limit(1).execute()
    if r.data:
        d = r.data[0]
        mj = d.get("metrics_json")
        sj = d.get("score_json")
        print(f"=== {sym} ===")
        if mj:
            print(f"  metrics keys: {list(mj.keys())[:8]}")
            print(f"  growth: {mj.get('growth')}")
            print(f"  quality: {mj.get('quality')}")
        else:
            print(f"  metrics: None")
        if sj:
            print(f"  score: overall={sj.get('overall_score')}")
        else:
            print(f"  score: None")
    else:
        print(f"{sym}: no cache")
