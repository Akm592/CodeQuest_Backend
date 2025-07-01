# `app/scrapers/leetcode_scraper.py` Documentation

## Overview

The `app/scrapers/leetcode_scraper.py` module is responsible for scraping LeetCode question details. It can handle various types of identifiers, including URLs, question numbers, and titles. It fetches the question content, difficulty, topics, and examples.

## Key Components

### `_fetch_all_problems() -> Optional[List[Dict[str, Any]]]`
- **Purpose**: Fetches and caches a list of all LeetCode problems.

### `normalize_text(text: str) -> str`
- **Purpose**: Normalizes text by lowercasing, removing accents, and extra whitespace.

### `get_title_slug(identifier: str) -> Optional[str]`
- **Purpose**: Resolves a LeetCode question identifier to a title slug.

### `fetch_leetcode_question(title_slug: str) -> Optional[str]`
- **Purpose**: Fetches the details of a LeetCode question using its title slug.

### `scrape_leetcode_question(identifier: str) -> Optional[str]`
- **Purpose**: The main function for scraping a LeetCode question.

### `extract_examples_from_content(content: str) -> List[Dict[str, Any]]]`
- **Purpose**: Extracts example inputs and outputs from the content of a LeetCode problem.

### `parse_output_data(output_text: str) -> Dict[str, Any]`
- **Purpose**: Parses the output text of a LeetCode example to extract the expected result.
