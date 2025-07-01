# `app/api/chat.py` Documentation

## Overview

The `app/api/chat.py` module is the core of the chatbot's functionality. It handles chat sessions, message processing, intent classification, and streaming responses. It also includes endpoints for managing chat sessions and scraping LeetCode questions.

## Key Components

### `router = APIRouter()`
- **Purpose**: Initializes an `APIRouter` instance for grouping chat-related API endpoints.

### `chat_memory = ChatMemory()`
- **Purpose**: Initializes a `ChatMemory` instance to manage chat sessions and their history.

### `classify_intent(query: str) -> str`
- **Purpose**: Determines the user's intent based on the content of their query.
- **Details**:
    - It classifies the intent into one of the following categories: `visualization`, `cs_tutor`, or `general`.

### `stream_response(...)`
- **Purpose**: Generates a streaming response for the user's input, handling various scenarios like LeetCode questions, visualizations, and general chat.

## API Endpoints

### `POST /scrape_leetcode`
- **Purpose**: Scrapes a LeetCode question based on a given identifier (URL, number, or title).

### `POST /chat`
- **Purpose**: The main endpoint for handling user chat interactions.

### `POST /sessions`
- **Purpose**: Creates a new chat session.

### `GET /sessions`
- **Purpose**: Retrieves all chat sessions for the current user.

### `GET /sessions/{session_id}/messages`
- **Purpose**: Retrieves all messages for a given chat session.
