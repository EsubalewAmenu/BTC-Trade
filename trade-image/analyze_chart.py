#!/usr/bin/env python3
import argparse
import base64
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = Path(__file__).with_name("screenshot") / "BTCUSDT_2026-08-25_09-51-56.png"
DEFAULT_CONTEXT = Path(__file__).with_name("system_context.txt")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name and name not in os.environ:
            os.environ[name] = value.strip().strip('"').strip("'")


def response_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        feedback = payload.get("promptFeedback") or payload.get("error") or payload
        raise ValueError(f"Gemini returned no candidate: {feedback}")
    parts = candidates[0].get("content", {}).get("parts", [])
    texts = [part["text"] for part in parts if isinstance(part.get("text"), str)]
    if not texts:
        raise ValueError("Gemini candidate contains no text")
    return "\n".join(texts).strip()


def parse_json_text(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    result = json.loads(cleaned)
    if not isinstance(result, dict):
        raise ValueError("Gemini result is not a JSON object")
    return result


def validate_result(result: dict) -> None:
    direction = str(result.get("direction", "")).upper()
    if direction not in {"WAIT", "LONG", "SHORT"}:
        raise ValueError("direction must be WAIT, LONG, or SHORT")
    confidence = float(result.get("confidence", -1))
    if not 0 <= confidence <= 100:
        raise ValueError("confidence must be between 0 and 100")
    candle_values = [result.get(name) for name in ("candle_high", "candle_low", "candle_close")]
    if any(value is None for value in candle_values):
        raise ValueError("candle_high, candle_low, and candle_close are required")
    high, low, close = map(float, candle_values)
    if high < low or not low <= close <= high:
        raise ValueError("invalid current candle high/low/close geometry")
    prices = [result.get(name) for name in ("entry_price", "stop_price", "target_price")]
    if direction == "WAIT":
        if any(value is not None for value in prices):
            raise ValueError("WAIT must use null entry, stop, and target")
        return
    if any(value is None for value in prices):
        raise ValueError("LONG/SHORT requires entry, stop, and target")
    entry, stop, target = map(float, prices)
    valid = stop < entry < target if direction == "LONG" else target < entry < stop
    if not valid:
        raise ValueError("invalid stop/entry/target geometry")
    calculated_rr = abs(target - entry) / abs(entry - stop)
    if calculated_rr < 2:
        raise ValueError(f"reward-to-risk is below 2.0: {calculated_rr:.3f}")


def analyze(
    image_path: Path, context_path: Path, model: str, timeout: int,
    open_trade: Optional[dict] = None,
) -> tuple[dict, dict]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing from the environment or root .env")
    if not image_path.is_file():
        raise FileNotFoundError(image_path)
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    if not mime_type.startswith("image/"):
        raise ValueError(f"Unsupported image MIME type: {mime_type}")
    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    user_prompt = (
        "Analyze this chart screenshot under the field guide. First inspect visible "
        "structure, EMA50, volume, pullback location, confirmation, invalidation, "
        "and realistic 2R space. Read candle_high, candle_low, candle_close and "
        "candle_utc from the rightmost fully revealed replay candle and visible OHLC legend. "
        "Return only the required JSON object."
    )
    if open_trade:
        user_prompt += (
            " An existing paper position is open, so direction must be WAIT and no new signal "
            f"may be proposed until it closes. Open trade: {json.dumps(open_trade)}"
        )
    request_body = {
        "systemInstruction": {"parts": [{"text": context_path.read_text(encoding="utf-8")}]},
        "contents": [{
            "role": "user",
            "parts": [
                {"inlineData": {"mimeType": mime_type, "data": image_data}},
                {"text": user_prompt},
            ],
        }],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    request = Request(
        endpoint,
        data=json.dumps(request_body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Gemini request failed: {exc.reason}") from exc
    result = parse_json_text(response_text(raw))
    validate_result(result)
    return result, raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one chart screenshot to Gemini")
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-3.6-flash"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("output"))
    args = parser.parse_args()
    load_env(ROOT / ".env")
    try:
        result, raw = analyze(args.image, args.context, args.model, args.timeout)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "gemini_raw_response.json").write_text(
        json.dumps(raw, indent=2), encoding="utf-8"
    )
    result_path = args.output_dir / "chart_decision.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"Saved: {result_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
