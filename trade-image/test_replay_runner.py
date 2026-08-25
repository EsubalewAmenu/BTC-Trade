import unittest

from replay_runner import FORWARD_SELECTORS, cached_forward_ready, is_forward_ready


class FakeElement:
    def __init__(self, classes="", aria_disabled="false", displayed=True, enabled=True):
        self.values = {"class": classes, "aria-disabled": aria_disabled}
        self.displayed = displayed
        self.enabled = enabled

    def get_attribute(self, name):
        return self.values.get(name)

    def is_displayed(self):
        return self.displayed

    def is_enabled(self):
        return self.enabled


class StaleElement(FakeElement):
    def get_attribute(self, name):
        raise RuntimeError("stale")


class ReplayRunnerTests(unittest.TestCase):
    def test_supports_tradingview_dynamic_tooltip(self):
        self.assertIn("[data-tooltip*='Forward']", FORWARD_SELECTORS)

    def test_enabled_forward_is_ready(self):
        self.assertTrue(is_forward_ready(FakeElement("controls__button button")))

    def test_tradingview_disabled_class_is_not_ready(self):
        self.assertFalse(is_forward_ready(FakeElement("button isDisabled")))

    def test_aria_disabled_is_not_ready(self):
        self.assertFalse(is_forward_ready(FakeElement(aria_disabled="true")))

    def test_cached_forward_survives_missing_title(self):
        self.assertTrue(cached_forward_ready(FakeElement(classes="button")))

    def test_stale_cached_forward_requests_rediscovery(self):
        self.assertFalse(cached_forward_ready(StaleElement()))


if __name__ == "__main__":
    unittest.main()
