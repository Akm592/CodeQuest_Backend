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
<<<<<<< Updated upstream
=======

    # fetch_leetcode_question will fetch details and log its own success/failure
    return await fetch_leetcode_question(title_slug)



def extract_examples_from_content(content: str) -> List[Dict[str, Any]]:
    """
    Extract example inputs and outputs from LeetCode problem content.
    Returns a list of examples with input and output data.
    """
    examples = []
    
    if not content:
        return examples
    
    # Pattern to match Example blocks
    example_pattern = r'Example\s*(\d+):\s*(.*?)(?=Example\s*\d+:|$)'
    example_matches = re.findall(example_pattern, content, re.DOTALL | re.IGNORECASE)
    
    for example_num, example_content in example_matches:
        example_data = {
            "example_number": int(example_num),
            "raw_content": example_content.strip(),
            "input": None,
            "output": None,
            "explanation": None
        }
        
        # Extract Input
        input_match = re.search(r'Input:\s*(.+?)(?=\n(?:Output|Explanation))', example_content, re.DOTALL)
        if input_match:
            input_text = input_match.group(1).strip()
            example_data["input"] = parse_input_data(input_text)
        
        # Extract Output
        output_match = re.search(r'Output:\s*(.+?)(?=\n(?:Explanation|Example|\Z))', example_content, re.DOTALL)
        if output_match:
            output_text = output_match.group(1).strip()
            example_data["output"] = parse_output_data(output_text)
        
        # Extract Explanation
        explanation_match = re.search(r'Explanation:\s*(.+?)(?=\nExample|\Z)', example_content, re.DOTALL)
        if explanation_match:
            example_data["explanation"] = explanation_match.group(1).strip()
        
        examples.append(example_data)
    
    return examples

def parse_input_data(input_text: str) -> Dict[str, Any]:
    """Parse input text to extract variable names and values."""
    parsed_input = {
        "raw": input_text,
        "variables": {}
    }
    
    # Common patterns for LeetCode inputs
    patterns = [
        # nums = [2,7,11,15], target = 9
        r'(\w+)\s*=\s*\[([^\]]+)\]',
        # target = 9
        r'(\w+)\s*=\s*([^\s,]+)',
        # s = "hello"
        r'(\w+)\s*=\s*"([^"]*)"',
        # grid = [["1","1","1"],["0","1","0"]]
        r'(\w+)\s*=\s*(\[\[.*?\]\])',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, input_text)
        for var_name, var_value in matches:
            try:
                # Try to evaluate as Python literal
                if var_value.startswith('['):
                    # Handle arrays
                    parsed_value = eval(var_value)
                elif var_value.startswith('"') and var_value.endswith('"'):
                    # Handle strings
                    parsed_value = var_value[1:-1]
                elif var_value.isdigit() or (var_value.startswith('-') and var_value[1:].isdigit()):
                    # Handle integers
                    parsed_value = int(var_value)
                elif '.' in var_value and var_value.replace('.', '').replace('-', '').isdigit():
                    # Handle floats
                    parsed_value = float(var_value)
                else:
                    parsed_value = var_value
                
                parsed_input["variables"][var_name] = parsed_value
            except:
                # If parsing fails, store as string
                parsed_input["variables"][var_name] = var_value
    
    return parsed_input

def parse_output_data(output_text: str) -> Dict[str, Any]:
    """Parse output text to extract expected result."""
    parsed_output = {
        "raw": output_text,
        "value": None
    }
    
    # Try to extract the actual output value
    # Remove common prefixes and clean up
    clean_output = re.sub(r'^(Output:\s*)?', '', output_text.strip())
    
    try:
        # Try to evaluate as Python literal
        if clean_output.startswith('[') or clean_output.startswith('{'):
            parsed_output["value"] = eval(clean_output)
        elif clean_output.startswith('"') and clean_output.endswith('"'):
            parsed_output["value"] = clean_output[1:-1]
        elif clean_output.isdigit() or (clean_output.startswith('-') and clean_output[1:].isdigit()):
            parsed_output["value"] = int(clean_output)
        elif clean_output.lower() in ['true', 'false']:
            parsed_output["value"] = clean_output.lower() == 'true'
        else:
            parsed_output["value"] = clean_output
    except:
        parsed_output["value"] = clean_output
    
    return parsed_output

async def fetch_leetcode_question(title_slug: str) -> Optional[Dict[str, Any]]:
    """
    Enhanced version that returns structured data including examples.
    """
    logger.info(f"Fetching LeetCode details for title slug: {title_slug}")
    query = """
    query getQuestionDetail($titleSlug: String!) {
      question(titleSlug: $titleSlug) {
        questionId
        questionFrontendId
        title
        content
        difficulty
        topicTags {
          name
          slug
        }
        codeSnippets {
            langSlug
            code
        }
      }
    }
    """
    variables = {"titleSlug": title_slug}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
        "Referer": f"https://leetcode.com/problems/{title_slug}/",
        "Origin": "https://leetcode.com",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                LEETCODE_GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers=headers
            )
            response.raise_for_status()

        data = response.json()

        if "errors" in data:
             logger.error(f"GraphQL error fetching '{title_slug}': {data['errors']}")
             return None

        if not data.get("data"):
             logger.error(f"GraphQL response missing 'data' field for '{title_slug}'.")
             return None

        question_data = data.get("data", {}).get("question")
        if not question_data:
            logger.warning(f"No question data found for slug '{title_slug}'.")
            return None

        # Extract basic info
        title = question_data.get('title', 'N/A')
        frontend_id = question_data.get('questionFrontendId', '')
        difficulty = question_data.get('difficulty', 'N/A')
        html_content = question_data.get("content", "")
        
        # Clean HTML content
        clean_content = "No description available."
        if html_content:
            try:
                soup = BeautifulSoup(html_content, "html.parser")
                for br in soup.find_all("br"):
                    br.replace_with("\n")
                for p in soup.find_all("p"):
                    p.append("\n\n")
                for li in soup.find_all("li"):
                    li.insert(0, "* ")
                    li.append("\n")
                for tag in soup(["script", "style"]):
                    tag.decompose()

                clean_content = soup.get_text(separator=" ").strip()
                clean_content = re.sub(r'\s*\n\s*', '\n', clean_content).strip()
                clean_content = re.sub(r'[ \t]{2,}', ' ', clean_content)
            except Exception as parse_error:
                logger.warning(f"Error parsing HTML content for {title_slug}: {parse_error}")
                clean_content = html_content

        # Extract examples from content
        examples = extract_examples_from_content(clean_content)
        
        # Add topic tags
        tags = [tag['name'] for tag in question_data.get('topicTags', []) if tag and 'name' in tag]
        tags_str = f"Topics: {', '.join(tags)}\n" if tags else ""

        # Return structured data instead of formatted string
        result = {
            "id": frontend_id,
            "title": title,
            "difficulty": difficulty,
            "tags": tags,
            "content": clean_content,
            "examples": examples,
            "formatted_content": (
                f"ID: {frontend_id}\n"
                f"Title: {title}\n"
                f"Difficulty: {difficulty}\n"
                f"{tags_str}"
                f"\nContent:\n{clean_content}"
            )
        }
        
        logger.info(f"Successfully fetched details for: {frontend_id}. {title} with {len(examples)} examples")
        return result

    except Exception as e:
        logger.error(f"Error fetching LeetCode question '{title_slug}': {e}", exc_info=True)
        return None

async def scrape_leetcode_question(identifier: str) -> Optional[Dict[str, Any]]:
    """
    Enhanced scraper that returns structured data with examples.
    """
    title_slug = await get_title_slug(identifier)
    if not title_slug:
        return None

>>>>>>> Stashed changes
    return await fetch_leetcode_question(title_slug)