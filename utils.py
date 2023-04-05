class PathMatchingTree:
    """
    PathMatchingTree is a data structure that can be used to match a path with a value.
    It supports exact match, partial match, and wildcard match.
    For example, if the tree is built with the following config:
      {
          "/foo/bar": "value1",
          "/baz/qux": "value2",
          "/foo/*": "value3",
          "/foo/*/bar": "value4"
      }
    Then the following path will match the corresponding value:
      /foo/bar -> value1
      /baz/qux -> value2
      /foo/baz -> value3
      /foo/baz/bar -> value4
      /foo/baz/bar2 -> value3
    """
    child = dict
    value = None

    def __init__(self, config):
        self.child = {}
        self._build_tree(config)

    def _build_tree(self, config):
        for k, v in config.items():
            parts = k.split('/')
            self._add(parts, v)

    def _add(self, parts, value):
        node = self
        for part in parts:
            if part == '':
                continue
            if part not in node.child:
                node.child[part] = PathMatchingTree(dict())
            node = node.child[part]
        node.value = value

    def get_matching(self, path):
        parts = path.split('/')
        matched = self
        for part in parts:
            if part == '':
                continue
            if part in matched.child:
                matched = matched.child[part]
            elif '*' in matched.child:
                matched = matched.child['*']
            else:
                break
        return matched.value
