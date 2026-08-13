"""Browser automation module for Upwork MCP."""

from .auth import login_interactive
from .client import UpworkBrowser

__all__ = ["UpworkBrowser", "login_interactive"]
