import hashlib
import json

import pytest


def host_text(response):
    """Encode a mocked model answer the way the real GenVM host returns it.

    For exec_prompt(response_format="json") the host hands the SDK the model's answer as text and the
    SDK parses it. genlayer-test (0.30.x) json-parses any str mock into a dict itself, which the current
    SDK rejects ("JSON result is not text"). Bytes skip that pre-parse and reach the SDK as text, so
    garbage and malformed answers still fail inside the SDK exactly as they would on a validator.
    """
    if isinstance(response, (bytes, bytearray)):
        return bytes(response)
    if isinstance(response, str):
        return response.encode("utf-8")
    return json.dumps(response).encode("utf-8")


@pytest.fixture
def actors(direct_accounts):
    """Ten framework accounts plus deterministic extras for capacity tests."""
    extra = [hashlib.sha256(f"errata-extra-{i}".encode()).digest()[:20] for i in range(12)]
    return list(direct_accounts) + extra


@pytest.fixture(autouse=True)
def _latest_mock_wins(direct_vm):
    """Make a later mock for the same pattern replace the earlier one.

    genlayer-test appends mocks and answers with the first match, so a test that serves a
    healthy page (or verdict) in its setup and then re-mocks the same pattern with a failure
    would keep receiving the healthy one. Replacing in place keeps hit-tracking indices stable.
    """
    original_web = direct_vm.mock_web
    original_llm = direct_vm.mock_llm

    def mock_web(url_pattern, response):
        for index, (pattern, old) in enumerate(direct_vm._web_mocks):
            if pattern.pattern == url_pattern and old.get("method", "GET") == response.get("method", "GET"):
                direct_vm._web_mocks[index] = (pattern, response)
                return
        original_web(url_pattern, response)

    def mock_llm(prompt_pattern, response):
        for index, (pattern, _old) in enumerate(direct_vm._llm_mocks):
            if pattern.pattern == prompt_pattern:
                direct_vm._llm_mocks[index] = (pattern, host_text(response))
                return
        original_llm(prompt_pattern, host_text(response))

    direct_vm.mock_web = mock_web
    direct_vm.mock_llm = mock_llm
    yield
