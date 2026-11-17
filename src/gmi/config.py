"""Read credentials and save them with private permissions."""

import json
import os
from pathlib import Path
import tempfile

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


class GMIError(Exception):
    """A safe, actionable error that can be shown to the user."""


BUILTIN_DEFAULT_MODEL = "openai/gpt-6.1-sol"


def config_path():
    home = os.environ.get("HOME")
    return Path(home or Path.home()) / ".config" / "gmi" / "config.toml"


def read_key():
    return read_config().get("api_key")


def read_config():
    try:
        with config_path().open("rb") as handle:
            values = tomllib.load(handle)
        key = values.get("api_key")
        if key is not None and not isinstance(key, str):
            raise GMIError("Invalid config; run gmi auth login --stdin to replace it.")
        for name in ("default_model", "default_image_model", "default_video_model", "default_audio_model", "sandbox_idc"):
            value = values.get(name)
            if value is not None and not isinstance(value, str):
                raise GMIError(f"Invalid config; run gmi config set {name} <id> to replace it.")
        return values
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise GMIError("Cannot read config; run gmi auth login --stdin to replace it.") from None


def resolve_key(flag=None):
    key = flag or os.environ.get("GMI_API_KEY") or read_key()
    if not key or not key.strip():
        raise GMIError("Missing API key; run gmi auth login or set GMI_API_KEY.")
    return key.strip()


def resolve_model(flag=None):
    return flag or os.environ.get("GMI_MODEL") or read_config().get("default_model") or BUILTIN_DEFAULT_MODEL


def mask_key(key):
    return "gmi_..." + (key[-4:] if len(key) > 8 else "****")


def save_key(key):
    save_config(api_key=key)


def save_config(api_key=None, default_model=None, unset_default=False, **settings):
    path = config_path()
    temporary = None
    try:
        current = read_config() if path.exists() else {}
        if api_key is not None:
            current["api_key"] = api_key
        if unset_default:
            current.pop("default_model", None)
        elif default_model is not None:
            current["default_model"] = default_model
        for name, value in settings.items():
            if value is not None:
                current[name] = value
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for name in ("api_key", "default_model", "default_image_model", "default_video_model", "default_audio_model", "sandbox_idc"):
                if name in current:
                    # JSON string escaping is compatible with TOML basic strings.
                    handle.write(name + " = " + json.dumps(current[name], ensure_ascii=False) + "\n")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except OSError:
        raise GMIError("Cannot save credentials; check permissions on ~/.config/gmi.") from None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
