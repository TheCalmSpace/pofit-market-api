from app.repositories.stock_repository import StockRepository

repo = StockRepository()

print("Searching Reliance...\n")

stocks = repo.search("reliance", country="IN")

for stock in stocks:
    print(stock)