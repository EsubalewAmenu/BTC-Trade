import json
import logging
from dataclasses import dataclass

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
    def __init__(self, api_key: str, model: str, required: bool):
        self.client = OpenAI(api_key=api_key) if api_key else None
        self.model = model
        self.required = required

    def decide(self, analysis) -> Decision:
        if not self.client:
            if self.required:
                return Decision("WAIT", 0, "LLM unavailable; fail-closed")
            return Decision(analysis.rule_signal, 1, "deterministic fallback")
        prompt = {
            "task": "Assess one BTCUSDT intraday paper-trade setup on a 15-minute execution timeframe.",
            "constraints": [
                "Prefer WAIT when evidence conflicts or is weak.",
                "Do not invent news, prices, indicators, or account facts.",
                "A LONG must not conflict with a DOWN 1h trend.",
                "A SHORT must not conflict with an UP 1h trend.",
                "This is a paper decision; position sizing and exits are handled by deterministic code.",
            ],
            "market": analysis.to_dict(),
        }
        try:
            response = self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "developer", "content": "Return only the requested structured trade decision."},
                    {"role": "user", "content": json.dumps(prompt, separators=(",", ":"))},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "btc_trade_decision",
                        "strict": True,
                        "schema": SCHEMA,
                    }
                },
                store=False,
            )
            result = json.loads(response.output_text)
            return Decision(result["action"], float(result["confidence"]), result["rationale"])
        except Exception as exc:
            LOG.exception("LLM decision failed")
            return Decision("WAIT", 0, f"LLM error: {type(exc).__name__}")
