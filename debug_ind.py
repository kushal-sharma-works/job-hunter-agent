# debug_ind.py
import requests
from bs4 import BeautifulSoup

url = "https://ind.nl/en/public-register-recognised-sponsors/public-register-work"
r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
soup = BeautifulSoup(r.text, "html.parser")

tables = soup.find_all("table")
print(f"Tables found: {len(tables)}")

for i, table in enumerate(tables):
    rows = table.find_all("tr")
    print(f"\nTable {i}: {len(rows)} rows")
    # Print first 5 rows raw
    for row in rows[:5]:
        cells = row.find_all(["td", "th"])
        print(f"  {[c.get_text(strip=True) for c in cells]}")