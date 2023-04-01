class PathMatchingTree:
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


if __name__ == "__main__":
    proxied_hosts = PathMatchingTree({
        "/": "https://api.openai.com",
        "/backend-api/conversation": "https://chat.openai.com",
    })
    print(proxied_hosts.get_matching("/v1/completions"))
    print(proxied_hosts.get_matching("/backend-api/conversation"))
