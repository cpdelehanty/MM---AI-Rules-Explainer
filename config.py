"""
Central config for external-service model names.

All modules should import CLAUDE_MODEL and VOYAGE_MODEL from here rather than
hardcoding. When Anthropic or Voyage retires a model, patching the env var (or
this file's defaults) is a one-line change.

Env vars take precedence; defaults are the current production-safe versions.
"""
import os

from dotenv import load_dotenv

load_dotenv(override=False)

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")
VOYAGE_MODEL = os.environ.get("VOYAGE_MODEL", "voyage-3")
