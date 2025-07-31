# Custom exceptions
class APIError(Exception):
    """Custom exception for API-related errors."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API Error: Status Code {status_code}, Detail: {detail}")

