"""Private provider evidence, deliberately excluded from model/API JSON."""

from copy import deepcopy


class ProviderResult(dict):
    def __init__(self, payload, *, receipt=None, original_result=None):
        super().__init__(payload)
        self.receipt = deepcopy(receipt)
        self.original_result = deepcopy(
            dict(payload) if original_result is None else original_result
        )
