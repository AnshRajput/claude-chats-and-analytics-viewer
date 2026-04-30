from claude_conversation_viewer import __version__


def test_version_exists():
    assert __version__ is not None


def test_version_format():
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
