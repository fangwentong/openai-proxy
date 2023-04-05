import unittest
from utils import PathMatchingTree


class TestPathMatchingTree(unittest.TestCase):

    def test_get_matching_exact_match(self):
        config = {
            "foo/bar": "value1",
            "baz/qux": "value2"
        }
        pmt = PathMatchingTree(config)
        result = pmt.get_matching("foo/bar")
        self.assertEqual(result, "value1")

    def test_get_matching_partial_match(self):
        config = {
            "foo/bar": "value1",
            "baz/qux": "value2"
        }
        pmt = PathMatchingTree(config)
        self.assertIsNone(pmt.get_matching("foo"))

    def test_get_matching_wildcard_match(self):
        config = {
            "/foo/*": "value1",
            "/baz/qux": "value2"
        }
        pmt = PathMatchingTree(config)
        self.assertEqual(pmt.get_matching("foo/bar"), "value1")

    def test_get_matching_multiple_wildcard_match(self):
        config = {
            "/foo/*": "value1",
            "/foo/*/bar": "value2"
        }
        pmt = PathMatchingTree(config)
        self.assertIsNone(pmt.get_matching("/foo"))
        self.assertEqual(pmt.get_matching("/foo/baz"), "value1")
        self.assertEqual(pmt.get_matching("/foo/baz/bar2"), "value1")
        self.assertEqual(pmt.get_matching("/foo/baz/bar"), "value2")

    def test_get_matching_no_match(self):
        config = {
            "/foo/bar": "value1",
            "/baz/qux": "value2"
        }
        pmt = PathMatchingTree(config)
        self.assertIsNone(pmt.get_matching("/foo"))
        self.assertIsNone(pmt.get_matching("/baz"))

    def test_get_matching_empty_string_match(self):
        config = {
            "/": "value1"
        }
        pmt = PathMatchingTree(config)
        self.assertEqual(pmt.get_matching("/"), "value1")
        self.assertEqual(pmt.get_matching("/test"), "value1")


if __name__ == "__main__":
    unittest.main()
