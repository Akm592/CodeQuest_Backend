# Backend Setup and Run Guide

## Prerequisites

Before setting up the backend, ensure you have the following installed on your system:

- **Python 3.10** or higher.
- **GCC Compiler**: Required for building the `pyroaring` dependency used by Supabase.
  - **Ubuntu/Debian**: `sudo apt install gcc`
  - **Fedora/RHEL**: `sudo dnf install gcc`
  - **macOS**: `xcode-select --install`

## Setup Instructions

1.  **Navigate to the backend directory:**

    ```bash
    cd /home/ashish/Documents/my_projects/CodeQuest_Backend
    ```

2.  **Create a Virtual Environment:**

    ```bash
    python3 -m venv venv
    ```

3.  **Activate the Virtual Environment:**

    ```bash
    source venv/bin/activate
    ```

4.  **Install Application Dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

    > **Note:** If you encounter an error related to `pyroaring` or `gcc`, ensure you have installed GCC as mentioned in the prerequisites, then try running the install command again.

## Configuration

1.  **Environment Variables**:
    Ensure you have a `.env` file in the root directory (`/home/ashish/Documents/my_projects/CodeQuest_Backend/.env`) with the following keys:

    ```env
    GEMINI_API_KEY="your_api_key_here"
    SUPABASE_URL="your_supabase_url"
    SUPABASE_KEY="your_supabase_anon_key"
    ```

## Running the Server

Start the backend server using `uvicorn`:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --env-file .env
```

- **--reload**: Enables auto-reload on code changes (useful for development).
- **--env-file .env**: Explicitly loads the environment variables.

## Verification

Once the server is running, you can access the API documentation to verify it's working:

- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

## Troubleshooting

-   **"ModuleNotFoundError: No module named 'supabase'"**: This means the installation failed. Check if `gcc` is installed and run `pip install supabase` again.
-   **"command 'gcc' failed"**: You are missing the C compiler. Install it using your system's package manager.
