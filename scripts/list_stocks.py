import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

r = sb.table("stocks").select("symbol,company_name,exchange,country,sector,industry").eq("country", "IN").order("symbol").limit(30).execute()
for row in r.data:
    print(f"{row['symbol']:15} {row['company_name']:40} {row['exchange']:5} {row['sector'] or '':25}")
