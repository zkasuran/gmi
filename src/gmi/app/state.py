"""Conversation persistence and usage accounting, independent of the terminal."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile

from ..config import BUILTIN_DEFAULT_MODEL, GMIError, config_path


def session_directory():
    return config_path().parent / "sessions"


def session_path(name):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in (".", ".."):
        raise GMIError("Use a session name containing letters, numbers, dots, underscores or hyphens.")
    return session_directory() / (name + ".json")


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    unknown_replies: int = 0

    def add(self, usage):
        incoming = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        outgoing = usage.get("completion_tokens") if isinstance(usage, dict) else None
        if type(incoming) is int and incoming >= 0:
            self.input += incoming
        if type(outgoing) is int and outgoing >= 0:
            self.output += outgoing
        if type(incoming) is not int or type(outgoing) is not int:
            self.unknown_replies += 1


@dataclass
class Conversation:
    model: str = BUILTIN_DEFAULT_MODEL
    messages: list = field(default_factory=list)
    system: str = ""
    usage: Usage = field(default_factory=Usage)

    def save(self, name=""):
        name = name or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        path = session_path(name)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".session-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"model": self.model, "system": self.system, "messages": self.messages}, handle)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return name

    def load(self, name):
        try:
            value = json.loads(session_path(name).read_text(encoding="utf-8"))
            model, system, messages = value["model"], value["system"], value["messages"]
            if not isinstance(model, str) or not model or not isinstance(system, str) or not isinstance(messages, list):
                raise ValueError
            if any(not isinstance(m, dict) or m.get("role") not in ("user", "assistant")
                   or not isinstance(m.get("content"), str) for m in messages):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            raise GMIError("Cannot load session; check the name and saved JSON.") from None
        self.model, self.system, self.messages = model, system, messages
