import unittest


class CIRedTest(unittest.TestCase):
    def test_fails_on_purpose(self):
        self.fail("Deliberate failure to check that CI goes red")
