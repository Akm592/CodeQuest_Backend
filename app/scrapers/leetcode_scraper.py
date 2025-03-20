# app/scrapers/leetcode_scraper.py
# this file contains a function that scrapes a LeetCode question
# using the GraphQL API.
#
# Implement the `scrape_leetcode_question` function that takes a LeetCode problem URL as input      
# and returns a string with the question title, difficulty, and description.
# The function should query the LeetCode GraphQL endpoint and extract the necessary data.
# app/scrapers/leetcode_scraper.py
import httpx
import re
from bs4 import BeautifulSoup
from typing import Optional

LEETCODE_GRAPHQL_URL = "https://leetcode.com/graphql"

async def get_title_slug(identifier: str) -> Optional[str]:
    """
    Resolve a LeetCode question identifier (URL, number, or title) to a title slug.
    """
    # Case 1: URL
    if "leetcode.com/problems/" in identifier:
        match = re.search(r"leetcode\.com/problems/([^/]+)/?", identifier)
        if match:
            return match.group(1)
    
    # Fetch the full problems list for name or number resolution
    async with httpx.AsyncClient() as client:
        response = await client.get("https://leetcode.com/api/problems/all/")
        if response.status_code != 200:
            return None
        problems = response.json()["stat_status_pairs"]
        
        # Case 2: Question number (e.g., "1")
        if identifier.isdigit():
            for problem in problems:
                if str(problem["stat"]["frontend_question_id"]) == identifier:
                    return problem["stat"]["question__title_slug"]
        
        # Case 3: Question title (e.g., "Two Sum")
        else:
            for problem in problems:
                if problem["stat"]["question__title"].lower() == identifier.lower():
                    return problem["stat"]["question__title_slug"]
    
    return None

async def fetch_leetcode_question(title_slug: str) -> Optional[str]:
    """
    Fetch question details from LeetCode GraphQL API using a title slug.
    """
    query = """
    query getQuestionDetail($titleSlug: String!) {
      question(titleSlug: $titleSlug) {
        questionId
        title
        content
        difficulty
        topicTags {
          name
        }
      }
    }
    """
    variables = {"titleSlug": title_slug}
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            LEETCODE_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers=headers
        )
    
    if response.status_code != 200:
        return None
    
    data = response.json()
    question_data = data.get("data", {}).get("question")
    if not question_data:
        return None
    
    soup = BeautifulSoup(question_data.get("content", ""), "html.parser")
    clean_content = soup.get_text(separator="\n").strip()
    
    result = (
        f"Title: {question_data.get('title', '')}\n"
        f"Difficulty: {question_data.get('difficulty', '')}\n"
        f"Content:\n{clean_content}"
    )
    return result

async def scrape_leetcode_question(identifier: str) -> Optional[str]:
    """
    Scrape a LeetCode question given an identifier (URL, question number, or title).
    Returns a string with the question title, difficulty, and description.
    """
    title_slug = await get_title_slug(identifier)
    if not title_slug:
        return None
    return await fetch_leetcode_question(title_slug)