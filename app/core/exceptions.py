# Custom exceptions 
class APIException(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API Exception: Status Code {status_code}, Detail: {detail}")