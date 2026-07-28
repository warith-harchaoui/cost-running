"""Tests for infrastructure.github — URL parsing and ref detection."""

import pytest

from cost_running.infrastructure.github import is_github_ref, parse_github_ref


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("https://github.com/karpathy/nanoGPT", ("karpathy", "nanoGPT")),
        ("https://github.com/karpathy/nanoGPT.git", ("karpathy", "nanoGPT")),
        ("https://github.com/karpathy/nanoGPT/", ("karpathy", "nanoGPT")),
        ("github.com/openai/whisper", ("openai", "whisper")),
        ("karpathy/nanoGPT", ("karpathy", "nanoGPT")),
        ("warith-harchaoui/cost-running", ("warith-harchaoui", "cost-running")),
    ],
)
def test_parse_github_ref_valid(ref, expected):
    assert parse_github_ref(ref) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "/home/user/project",
        ".",
        "./myrepo",
        "not-a-slug",
        "https://gitlab.com/owner/repo",
        "",
    ],
)
def test_parse_github_ref_invalid(bad):
    with pytest.raises(ValueError):
        parse_github_ref(bad)


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("https://github.com/karpathy/nanoGPT", True),
        ("github.com/openai/whisper", True),
        ("karpathy/nanoGPT", True),
        ("/home/user/project", False),
        (".", False),
        ("cost_of_running.yaml", False),
        ("not-a-slug", False),
    ],
)
def test_is_github_ref(ref, expected):
    assert is_github_ref(ref) == expected
