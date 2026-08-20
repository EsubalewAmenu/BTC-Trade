import json
import logging
from dataclasses import dataclass

from google import genai
from openai import OpenAI


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Decision:
    action: str
    confidence: float
    rationale: str


SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["LONG", "SHORT", "WAIT"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string", "maxLength": 240},
    },
    "required": ["action", "confidence", "rationale"],
    "additionalProperties": False,
}


class LLMDecider:
    def __init__(
        self,
        openai_api_key: str,
        openai_model: str,
        gemini_api_key: str,
        gemini_model: str,
        use_gemini: bool,
        required: bool,
    ):
        self.openai_client = OpenAI(api_key=openai_api_key) if openai_api_key else None
        self.gemini_client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None
        self.openai_model = openai_model
        self.gemini_model = gemini_model
        self.use_gemini = use_gemini
        self.required = required

    @property
    def provider(self):
        return "gemini" if self.use_gemini else "openai"

    def _prompt(self, analysis):
        return {
            "task": "Assess one BTCUSDT intraday paper-trade setup on a 15-minute execution timeframe.",
            "constraints": [
                "Prefer WAIT when evidence conflicts or is weak.",
                "Do not invent news, prices, indicators, or account facts.",
                "A LONG must not conflict with a DOWN 1h trend.",
                "A SHORT must not conflict with an UP 1h trend.",
                "This is a paper decision; deterministic code controls position sizing and exits.",
            ],
            "market": analysis.to_dict(),
        }

    def _openai_decision(self, prompt):
        if not self.openai_client:
            raise ValueError("OpenAI client is not configured")
        response = self.openai_client.responses.create(
            model=self.openai_model,
            input=[
                {"role": "developer", "content": "Return only the requested structured trade decision."},
                {"role": "user", "content": json.dumps(prompt, separators=(",", ":"))},
            ],
            text={
                "format": {
                    "type": "json_schema", "name": "btc_trade_decision",
                    "strict": True, "schema": SCHEMA,
                }
            },
            store=False,
        )
        return json.loads(response.output_text)

    def _gemini_decision(self, prompt):
        if not self.gemini_client:
            raise ValueError("Gemini client is not configured")
        interaction = self.gemini_client.interactions.create(
            model=self.gemini_model,
            input=json.dumps(prompt, separators=(",", ":")),
            response_format={
                "type": "text", "mime_type": "application/json", "schema": SCHEMA,
            },
        )
        return json.loads(interaction.output_text)

    def verify_connection(self) -> Decision:
        """Make one real structured request to verify the selected provider at startup."""
        prompt = {
            "task": "Verify the BTCUSDT paper trader's structured-output connection.",
            "instructions": "Return action WAIT, confidence 1, and a short connectivity confirmation.",
            "market_data": "No market decision is requested.",
        }
        result = self._gemini_decision(prompt) if self.use_gemini else self._openai_decision(prompt)
        action = result["action"]
        confidence = float(result["confidence"])
        rationale = result["rationale"]
        if action != "WAIT" or not 0 <= confidence <= 1 or not isinstance(rationale, str):
            raise ValueError("Provider returned an invalid verification response")
        return Decision(action, confidence, f"[{self.provider}] {rationale}")

    def decide(self, analysis) -> Decision:
        selected_client = self.gemini_client if self.use_gemini else self.openai_client
        if not selected_client:
            if self.required:
                return Decision("WAIT", 0, f"[{self.provider}] unavailable; fail-closed")
            return Decision(analysis.rule_signal, 1, "[rules] deterministic fallback")
        try:
            prompt = self._prompt(analysis)
            result = self._gemini_decision(prompt) if self.use_gemini else self._openai_decision(prompt)
            return Decision(
                result["action"], float(result["confidence"]),
                f"[{self.provider}] {result['rationale']}",
            )
        except Exception as exc:
            LOG.exception("%s decision failed", self.provider)
            return Decision("WAIT", 0, f"[{self.provider}] error: {type(exc).__name__}")
