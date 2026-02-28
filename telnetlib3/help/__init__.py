"""Help topic loader for telnetlib3 TUI."""

# std imports
import importlib.resources


def _read_topic(name: str) -> str:
    """Read a help topic markdown file by name (without .md extension)."""
    ref = importlib.resources.files(__package__).joinpath(f"{name}.md")
    return ref.read_text(encoding="utf-8")


def get_help(topic: str) -> str:
    """
    Return combined help text for a TUI help topic.

    :param str topic: One of ``"macro"``, ``"autoreply"``, ``"highlight"``,
        ``"room"``, or ``"keybindings"``.
    :rtype: str
    """
    commands = _read_topic("commands")
    if topic == "macro":
        return _read_topic("macros") + "\n---\n\n" + commands
    if topic == "autoreply":
        return _read_topic("autoreplies") + "\n---\n\n" + commands
    if topic == "highlight":
        return _read_topic("highlights")
    if topic == "room":
        return _read_topic("rooms")
    if topic == "keybindings":
        return _read_topic("keybindings")
    raise ValueError(f"unknown help topic: {topic!r}")
