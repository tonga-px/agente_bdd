import json
import logging
import re

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"

_JSON_RE = re.compile(r"\{[^{}]*\}")


class ClaudeService:
    def __init__(self, api_key: str):
        self._client = AsyncAnthropic(api_key=api_key)

    async def analyze(
        self, system_prompt: str, user_prompt: str
    ) -> dict | None:
        try:
            response = await self._client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = response.content[0].text
            return self._try_parse_json(text)
        except Exception:
            logger.exception("Claude API call failed")
            return None

    @staticmethod
    def _try_parse_json(text: str) -> dict | None:
        # Strip markdown fences
        stripped = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`")

        # Try direct parse
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: find JSON object in the text
        match = _JSON_RE.search(text)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                pass

        return None
