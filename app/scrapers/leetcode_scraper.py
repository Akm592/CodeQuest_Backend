# app/scrapers/leetcode_scraper.py
import httpx
import re
import unicodedata
from bs4 import BeautifulSoup
from typing import Optional, List, Dict, Any
from app.core.logger import logger # Make sure logger is configured in app.core

LEETCODE_GRAPHQL_URL = "https://leetcode.com/graphql"
LEETCODE_ALL_PROBLEMS_URL = "https://leetcode.com/api/problems/all/"

# Cache for problems list to avoid repeated fetching
_problems_cache: Optional[List[Dict[str, Any]]] = None

async def _fetch_all_problems() -> Optional[List[Dict[str, Any]]]:
    """Fetches and caches the list of all LeetCode problems."""
    global _problems_cache
    if _problems_cache:
        return _problems_cache

    logger.info("Fetching all LeetCode problems list...")
    try:
        # Increased timeout for potentially large response
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(LEETCODE_ALL_PROBLEMS_URL)
            response.raise_for_status() # Raise exception for bad status codes (4xx or 5xx)
            data = response.json()
            if "stat_status_pairs" in data:
                _problems_cache = data["stat_status_pairs"]
                logger.info(f"Successfully fetched and cached {len(_problems_cache)} LeetCode problems.")
                return _problems_cache
            else:
                logger.error("Fetched LeetCode problems data missing 'stat_status_pairs'.")
                _problems_cache = [] # Set to empty list to avoid retrying immediately
                return None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP status error fetching LeetCode problems: {e.response.status_code} - {e.request.url}")
        _problems_cache = []
        return None
    except httpx.RequestError as e:
        logger.error(f"HTTP request error fetching LeetCode problems: {e}")
        _problems_cache = []
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching LeetCode problems: {e}", exc_info=True)
        _problems_cache = []
        return None

def normalize_text(text: str) -> str:
    """Normalize text by lowercasing, removing accents, and extra whitespace."""
    if not isinstance(text, str):
        return ""
    try:
        # NFKD decomposition followed by encoding/decoding removes accents
        text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    except Exception:
        pass # Ignore normalization errors for weird characters
    text = text.lower()
    text = re.sub(r'\s+', ' ', text).strip() # Replace multiple spaces with one
    return text

async def get_title_slug(identifier: str) -> Optional[str]:
    """
    Resolve a LeetCode question identifier (URL, number, title, combined, or pasted) to a title slug.
    """
    if not identifier or not isinstance(identifier, str):
        logger.warning("Invalid identifier provided to get_title_slug: must be a non-empty string.")
        return None

    normalized_identifier = normalize_text(identifier)
    logger.info(f"Attempting to resolve identifier: '{identifier[:100]}...' (normalized: '{normalized_identifier[:100]}...')")

    # 1. Check for URL (more robustly handles query params etc.)
    url_match = re.search(r"leetcode\.com/problems/([^/?#]+)", identifier)
    if url_match:
        title_slug = url_match.group(1)
        logger.info(f"Identified as URL, extracted title slug: {title_slug}")
        # Optional: Verify slug exists via a quick HEAD request or basic fetch?
        # For now, trust the extracted slug.
        return title_slug

    problems = await _fetch_all_problems()
    if not problems:
        logger.error("Failed to fetch or use problem list for matching. Cannot resolve non-URL identifier.")
        # Maybe attempt GraphQL query directly if identifier *looks* like a slug?
        if re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", identifier.strip()):
             logger.info(f"Identifier '{identifier.strip()}' looks like a slug, attempting direct use.")
             return identifier.strip()
        return None # Cannot proceed reliably without the problem list

    matched_slug = None

    # 2. Check for "Number. Title" format or just Number
    num_title_match = re.match(r"^\s*(\d+)\s*[.]?\s*(.*)", identifier.strip())
    just_num_match = re.match(r"^\s*(\d+)\.?\s*$", identifier.strip()) # Handles optional trailing dot

    potential_number = None
    potential_title_part = ""

    if num_title_match:
        potential_number = num_title_match.group(1)
        # Use the normalized version of the potential title part
        potential_title_part = normalize_text(num_title_match.group(2))
        logger.info(f"Possible Number-Title format: Number={potential_number}, Title Part='{potential_title_part}'")
    elif just_num_match:
        potential_number = just_num_match.group(1)
        logger.info(f"Possible Number format: Number={potential_number}")

    # Match using number first if available (most reliable)
    if potential_number:
        for problem in problems:
            stat = problem.get("stat", {})
            # Use frontend_question_id as it's the displayed number
            if str(stat.get("frontend_question_id")) == potential_number:
                slug = stat.get("question__title_slug")
                if not slug: continue # Skip if slug is missing for this problem

                # If we also have a title part, verify it loosely matches
                if potential_title_part:
                    problem_title_norm = normalize_text(stat.get("question__title", ""))
                    # Check if normalized potential title is substring of normalized actual title or vice-versa
                    if potential_title_part in problem_title_norm or problem_title_norm in potential_title_part:
                        logger.info(f"Matched by number ({potential_number}) and verified title part: {slug}")
                        matched_slug = slug
                        break # Found best match
                    else:
                        logger.debug(f"Number {potential_number} matched slug {slug}, but title part '{potential_title_part}' didn't match problem title '{problem_title_norm}'")
                        # Don't break yet, maybe another problem has same number? (unlikely)
                        # If we don't find a better match, we might fallback to this later.
                        if not matched_slug: # Store first number match as fallback
                            matched_slug = slug

                else: # Matched by number only
                     logger.info(f"Matched by number ({potential_number}): {slug}")
                     matched_slug = slug
                     break # Found match

        if matched_slug:
            logger.info(f"Resolution successful based on number: {matched_slug}")
            return matched_slug # Return early if number match found

    # 3. Fuzzy Title Matching (if no number match or no number identified)
    # Only proceed if we haven't found a match via number
    logger.info("Number match failed or not applicable, attempting title matching...")
    # Use the potential title part from "Num. Title" or the full normalized identifier
    search_title = potential_title_part if potential_title_part else normalized_identifier

    # Avoid matching extremely short/common words if identifier is short
    if not search_title or (len(search_title) < 4 and len(identifier.split()) < 3):
         logger.info("Identifier too short or lacks specific terms for reliable title-only matching.")
    else:
        logger.info(f"Attempting fuzzy title matching with: '{search_title}'")
        # Look for exact title match first (case-insensitive via normalization)
        for problem in problems:
            stat = problem.get("stat", {})
            problem_title_norm = normalize_text(stat.get("question__title", ""))
            slug = stat.get("question__title_slug")
            if not slug or not problem_title_norm: continue

            if problem_title_norm == search_title:
                logger.info(f"Matched by exact title: {slug}")
                matched_slug = slug
                break
        # If no exact match, look for containment (problem title within identifier)
        # This handles cases like "solve two sum" matching "Two Sum"
        if not matched_slug:
             candidates = []
             for problem in problems:
                stat = problem.get("stat", {})
                problem_title_norm = normalize_text(stat.get("question__title", ""))
                slug = stat.get("question__title_slug")
                if not slug or not problem_title_norm: continue

                # Check if the actual problem title is a substring of the search title
                if problem_title_norm and problem_title_norm in search_title:
                    # Score based on length - longer matches are better
                    candidates.append({'slug': slug, 'title': problem_title_norm, 'score': len(problem_title_norm)})
                    logger.debug(f"Potential title match (title in identifier): {slug} (Title: {problem_title_norm})")

             # If multiple candidates, pick the one with the longest matching title
             if candidates:
                 candidates.sort(key=lambda x: x['score'], reverse=True)
                 matched_slug = candidates[0]['slug']
                 matched_title = candidates[0]['title']
                 logger.info(f"Matched by best fuzzy title (title in identifier): {matched_slug} (Matched Title: '{matched_title}')")

        if matched_slug:
            logger.info(f"Resolution successful based on title matching: {matched_slug}")
            return matched_slug

    # 4. Heuristic for Pasted Text (Check first few lines for pattern)
    # Only if identifier is long and no match found yet
    if not matched_slug and len(identifier) > 150: # Arbitrary length threshold
        logger.info("Identifier is long, attempting pasted text heuristic...")
        lines = identifier.strip().split('\n')
        for line in lines[:5]: # Check first 5 lines only
            # Look for "Number. Title" pattern at the start of a line
            pasted_match = re.match(r"^\s*(\d+)\.\s+([a-zA-Z0-9\s-]+)", line.strip())
            if pasted_match:
                num = pasted_match.group(1)
                title_part = normalize_text(pasted_match.group(2))
                logger.info(f"Pasted text heuristic found pattern: Number={num}, Title Part='{title_part}'")
                # Try matching this extracted info using the number logic again
                for problem in problems:
                     stat = problem.get("stat", {})
                     slug = stat.get("question__title_slug")
                     if not slug: continue

                     if str(stat.get("frontend_question_id")) == num:
                        problem_title_norm = normalize_text(stat.get("question__title", ""))
                        # Check if extracted title part matches loosely
                        if title_part in problem_title_norm or problem_title_norm in title_part:
                            logger.info(f"Matched via pasted text heuristic: {slug}")
                            matched_slug = slug
                            break # Found match

                if matched_slug:
                     logger.info(f"Resolution successful based on pasted text heuristic: {matched_slug}")
                     return matched_slug # Exit outer loop too
            # Stop checking lines if match found
            if matched_slug: break

    if not matched_slug:
        logger.warning(f"Could not resolve identifier to a LeetCode title slug: '{identifier[:100]}...'")

    return matched_slug


async def fetch_leetcode_question(title_slug: str) -> Optional[str]:
    """
    Fetch question details from LeetCode GraphQL API using a title slug.
    Returns a formatted string with details or None on failure.
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
        codeSnippets { # Optionally fetch code snippets
            langSlug
            code
        }
      }
    }
    """
    variables = {"titleSlug": title_slug}
    # Use headers that mimic a browser to reduce chance of being blocked
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
        "Referer": f"https://leetcode.com/problems/{title_slug}/", # Referer is often checked
        "Origin": "https://leetcode.com",
        # You might need 'x-csrftoken' if interacting with logged-in features, but usually not needed for public data
    }

    try:
        # Use a reasonable timeout
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                LEETCODE_GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers=headers
            )
            response.raise_for_status() # Check for HTTP errors like 4xx/5xx

        data = response.json()

        # Check for GraphQL specific errors returned in the JSON body
        if "errors" in data:
             logger.error(f"GraphQL error fetching '{title_slug}': {data['errors']}")
             return None

        if not data.get("data"):
             logger.error(f"GraphQL response missing 'data' field for '{title_slug}'. Response: {data}")
             return None

        question_data = data.get("data", {}).get("question")
        if not question_data:
            logger.warning(f"No question data found for slug '{title_slug}' in GraphQL response data.")
            # This might happen if the slug is valid but the question is hidden/premium?
            return None # Treat as not found

        # --- Format the output ---
        title = question_data.get('title', 'N/A')
        frontend_id = question_data.get('questionFrontendId', '')
        difficulty = question_data.get('difficulty', 'N/A')

        # Clean HTML content using BeautifulSoup
        html_content = question_data.get("content", "")
        clean_content = "No description available."
        if html_content:
            try:
                soup = BeautifulSoup(html_content, "html.parser")
                # Extract text, trying to preserve paragraphs and structure slightly better
                # Replace <p> with newline, <li> with "* ", etc.
                for br in soup.find_all("br"):
                    br.replace_with("\n")
                for p in soup.find_all("p"):
                    p.append("\n\n")
                for li in soup.find_all("li"):
                    li.insert(0, "* ")
                    li.append("\n")
                # Remove script/style tags
                for tag in soup(["script", "style"]):
                    tag.decompose()

                # Get text, strip extra whitespace but keep meaningful newlines
                clean_content = soup.get_text(separator=" ").strip()
                # Consolidate multiple newlines/spaces that might result
                clean_content = re.sub(r'\s*\n\s*', '\n', clean_content).strip()
                clean_content = re.sub(r'[ \t]{2,}', ' ', clean_content) # Consolidate spaces

            except Exception as parse_error:
                logger.warning(f"Error parsing HTML content for {title_slug}: {parse_error}. Falling back to raw content.")
                clean_content = html_content # Fallback

        # Add topic tags if available
        tags = [tag['name'] for tag in question_data.get('topicTags', []) if tag and 'name' in tag]
        tags_str = f"Topics: {', '.join(tags)}\n" if tags else ""

        # Construct the final result string
        result = (
            f"ID: {frontend_id}\n"
            f"Title: {title}\n"
            f"Difficulty: {difficulty}\n"
            f"{tags_str}"
            f"\nContent:\n{clean_content}"
        )
        logger.info(f"Successfully fetched and formatted details for: {frontend_id}. {title}")
        return result

    except httpx.HTTPStatusError as e:
        # Log specific HTTP errors
        logger.error(f"HTTP error fetching LeetCode question '{title_slug}': Status {e.response.status_code} - URL: {e.request.url}")
        try:
            # Try to log response body for debugging if available and not too large
            error_body = e.response.text
            logger.error(f"Response body: {error_body[:500]}{'...' if len(error_body) > 500 else ''}")
        except Exception:
            logger.error("Could not read error response body.")
        return None
    except httpx.RequestError as e:
        # Log connection errors, timeouts etc.
        logger.error(f"Request error fetching LeetCode question '{title_slug}': {e}")
        return None
    except Exception as e:
        # Log any other unexpected errors
        logger.error(f"Unexpected error fetching or processing LeetCode question '{title_slug}': {e}", exc_info=True)
        return None


async def scrape_leetcode_question(identifier: str) -> Optional[str]:
    """
    Scrape a LeetCode question given an identifier (URL, question number, title, combined, or pasted text).
    Returns a formatted string with the question details, or None if failed.
    """
    title_slug = await get_title_slug(identifier)
    if not title_slug:
        # get_title_slug already logged the failure reason
        return None
    
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
    return await fetch_leetcode_question(title_slug)