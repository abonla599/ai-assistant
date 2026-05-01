from typing import Any, Optional

class ToolResponse:
    def __init__(self, success: bool, data: Any = None, error: str = "", hint: str = ""):
        self.success = success
        self.data = data
        self.error = error
        self.hint = hint

    def to_string(self) -> str:
        if self.success:
            return f"✓ {self.data}"
        return f"✗ 错误: {self.error}。建议: {self.hint}"

    def to_dict(self):
        return {"success": self.success, "data": self.data, "error": self.error, "hint": self.hint}