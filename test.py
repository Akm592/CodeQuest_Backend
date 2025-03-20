import asyncio
import app.scrapers.leetcode_scraper as leetcode_scraper
from app.scrapers.leetcode_scraper import scrape_leetcode_question

url = "https://leetcode.com/problems/two-sum/"
result = asyncio.run(scrape_leetcode_question(url))
print(result)
