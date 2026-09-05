# CodeQuest_Backend

CodeQuest is an intelligent computer science tutoring chatbot designed to help users understand complex algorithms, solve LeetCode-style problems, and visualize data structures. This backend is built with Python, FastAPI, and Google's Gemini Pro.

## Features

*   **LLM-Powered Chat:** Core chat functionality is powered by Google's Gemini Pro for natural and helpful conversations.
*   **Intent Classification:** An LLM-based intent classification system understands whether the user wants to chat, get a CS explanation, or see a visualization.
*   **LeetCode Problem Scraper:** Automatically scrapes LeetCode problems from URLs or identifiers.
*   **Algorithm Visualization:** Generates JSON data for visualizing algorithms and data structures on the frontend.
*   **Chat Memory:** Maintains conversation history for contextual responses.
*   **Supabase Integration:** Uses Supabase for database storage of chat sessions and messages.
*   **Async Architecture:** Built with FastAPI and `asyncio` for high performance.

## Technologies Used

*   **Backend:** Python, FastAPI
*   **LLM:** Google Gemini Pro
*   **Database:** Supabase (PostgreSQL)
*   **Web Scraping:** `BeautifulSoup`, `httpx`
*   **Testing:** `pytest`, `pytest-asyncio`
*   **Linting:** `ruff`
*   **Deployment:** Docker

## Project Structure

```
/
├── app/                    # Core application code
│   ├── api/                # API endpoint routers
│   ├── core/               # Config, logging, auth, rate limiting
│   ├── database/           # Supabase client (scoped to the calling user)
│   ├── llm/                # Gemini integration and prompts
│   ├── memory/             # In-process chat session cache
│   ├── schemas/            # Pydantic models and payload validation
│   └── scrapers/           # LeetCode scraper
├── supabase/migrations/    # Database schema and RLS policies
├── tests/                  # Pytest test suite
├── Dockerfile              # Docker build file
├── generate_openapi.py     # OpenAPI schema generator
├── requirements.txt        # Pinned runtime dependencies
├── requirements-dev.txt    # Test and lint dependencies
└── pyproject.toml          # Ruff and pytest configuration
```

## Authentication & data protection

Requests with **no `Authorization` header** are guests: their sessions are
ephemeral, nothing is persisted, and they are rate limited. Requests carrying a
**valid Supabase JWT** are verified against Supabase, and every database query
runs as that user so Row Level Security applies. A **present but invalid** token
returns `401` rather than being silently downgraded to a guest.

Before pointing this at a real Supabase project, apply
`supabase/migrations/0001_chat_schema_rls.sql` — it defines the schema and the
RLS policies. The anon key is public by design (the frontend ships it), so RLS is
what actually protects user data. See [`supabase/README.md`](supabase/README.md).

## API Endpoints

### Chat

*   `POST /chat`
    *   **Description:** The main endpoint for sending a user message and receiving a streaming response.
    *   **Headers:** `X-Session-ID` (required) - A UUID for the chat session.
    *   **Request Body:**
        ```json
        {
          "user_input": "Your message here"
        }
        ```
    *   **Response:** A streaming response (`text/event-stream`) with Server-Sent Events (SSE).

### Session Management

*   `POST /sessions`
    *   **Description:** Creates a new chat session.
    *   **Response:**
        ```json
        {
          "session_id": "new-session-uuid"
        }
        ```
*   `GET /sessions`
    *   **Description:** Retrieves all chat sessions for the current user.
*   `GET /sessions/{session_id}/messages`
    *   **Description:** Retrieves all messages for a given chat session.

## Getting Started

### Prerequisites

*   Python 3.10+
*   An active Supabase project
*   A Google Gemini API key

### Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/CodeQuest_Backend.git
    cd CodeQuest_Backend
    ```

2.  **Create a virtual environment and install dependencies:**
    ```bash
    python -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

### Configuration

1.  Create a `.env` file in the root directory and add the following environment variables:

    ```
    GEMINI_API_KEY="your_gemini_api_key"
    SUPABASE_URL="your_supabase_project_url"
    SUPABASE_ANON_KEY="your_supabase_anon_key"
    ```

### Running the Application

1.  **Start the FastAPI server:**
    ```bash
    uvicorn app.main:app --reload
    ```
    The application will be available at `http://127.0.0.1:8000`.

## Running Tests

To run the test suite, use `pytest`:

```bash
pytest
```