import unittest

from replay_runner import is_forward_ready


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


class ReplayRunnerTests(unittest.TestCase):
    def test_enabled_forward_is_ready(self):
        self.assertTrue(is_forward_ready(FakeElement("controls__button button")))

    def test_tradingview_disabled_class_is_not_ready(self):
        self.assertFalse(is_forward_ready(FakeElement("button isDisabled")))

    def test_aria_disabled_is_not_ready(self):
        self.assertFalse(is_forward_ready(FakeElement(aria_disabled="true")))


if __name__ == "__main__":
    unittest.main()
