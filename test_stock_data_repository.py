from app.repositories.stock_data_repository import StockDataRepository

repo = StockDataRepository()

data = repo.get("RELIANCE")

print(data)