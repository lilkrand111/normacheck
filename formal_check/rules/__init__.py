"""Each rule retains its own ID, even when only part is machine-checkable."""
RULES = {}


def rule(rule_id):
    def register(func):
        if rule_id in RULES:
            raise ValueError("Duplicate rule " + rule_id)
        RULES[rule_id] = func
        return func
    return register


from . import basic, structure, objects, references  # noqa: E402,F401
