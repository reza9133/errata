import hashlib

import pytest


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
                direct_vm._llm_mocks[index] = (pattern, response)
                return
        original_llm(prompt_pattern, response)

    direct_vm.mock_web = mock_web
    direct_vm.mock_llm = mock_llm
    yield
