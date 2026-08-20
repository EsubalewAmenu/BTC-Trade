import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from llm_decider import LLMDecider


class LLMProviderTests(unittest.TestCase):
    @patch("llm_decider.genai.Client")
    @patch("llm_decider.OpenAI")
    def test_gemini_true_selects_gemini(self, openai, gemini):
        decider = LLMDecider("openai-key", "openai-model", "gemini-key", "gemini-model", True, True)
        self.assertEqual(decider.provider, "gemini")
        gemini.assert_called_once_with(api_key="gemini-key")
        openai.assert_called_once_with(api_key="openai-key")

    @patch("llm_decider.genai.Client")
    @patch("llm_decider.OpenAI")
    def test_gemini_false_selects_openai(self, openai, gemini):
        decider = LLMDecider("openai-key", "openai-model", "gemini-key", "gemini-model", False, True)
        self.assertEqual(decider.provider, "openai")


if __name__ == "__main__":
    unittest.main()
