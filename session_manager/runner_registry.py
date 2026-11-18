"""Process-local loading of built-ins and trusted user-local runner modules."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import pkgutil
import sys
from types import ModuleType

from .runner_api import Runner


class RunnerPluginError(ValueError):
    """A runner module could not be loaded or violated the public contract."""


class RunnerRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, Runner] = {}
        self._by_command: dict[str, Runner] = {}

    def _register(self, module: ModuleType, path: Path) -> None:
        def fail(reason: str) -> None:
            raise RunnerPluginError(f"{path}: {reason}")

        version = getattr(module, "RUNNER_API_VERSION", None)
        if type(version) is not int or version != 1:
            fail("RUNNER_API_VERSION must be integer 1")
        runner = getattr(module, "RUNNER", None)
        if not isinstance(runner, Runner):
            fail("RUNNER must be a concrete Runner instance")
        name = getattr(runner, "name", None)
        if not isinstance(name, str) or not name or any(c.isspace() for c in name):
            fail("RUNNER.name must be a nonempty canonical name without whitespace")
        commands = getattr(runner, "command_names", None)
        if not isinstance(commands, tuple) or not commands:
            fail("command_names must be a nonempty tuple of executable basenames")
        for command in commands:
            if (
                not isinstance(command, str) or not command
                or any(c.isspace() for c in command)
                or os.path.basename(command) != command
            ):
                fail("command_names must contain nonempty executable basenames")
        if len(set(commands)) != len(commands):
            fail("command_names must be unique")
        if name in self._by_name:
            fail(f"duplicate runner identity: {name}")
        for command in commands:
            if command in self._by_command:
                fail(f"executable already owned: {command}")
        try:
            new_command = runner.new_session_command()
        except Exception as exc:
            raise RunnerPluginError(f"{path}: new_session_command failed: {exc}") from exc
        if not isinstance(new_command, str) or not new_command.split():
            fail("new_session_command must identify an owned executable")
        if os.path.basename(new_command.split()[0]) not in commands:
            fail("new_session_command executable must belong to command_names")
        self._by_name[name] = runner
        for command in commands:
            self._by_command[command] = runner

    def get(self, name: str) -> Runner | None:
        return self._by_name.get(name)

    def all(self) -> tuple[Runner, ...]:
        return tuple(sorted(self._by_name.values(), key=lambda runner: (runner.order, runner.name)))

    def for_command(self, command: str) -> Runner | None:
        tokens = command.split()
        return self._by_command.get(os.path.basename(tokens[0])) if tokens else None


def _external_module(path: Path) -> ModuleType:
    resolved = path.resolve()
    digest = hashlib.sha256(os.fsencode(resolved)).hexdigest()
    name = f"_aisema_runner_{digest}"
    spec = importlib.util.spec_from_file_location(name, resolved)
    if spec is None or spec.loader is None:
        raise RunnerPluginError(f"{path}: cannot construct module loader")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def load_registry(plugin_dir: Path | None = None) -> RunnerRegistry:
    from . import runners

    registry = RunnerRegistry()
    for info in sorted(pkgutil.iter_modules(runners.__path__), key=lambda item: item.name):
        if info.name.startswith("_"):
            continue
        path = Path(runners.__path__[0]) / f"{info.name}.py"
        try:
            module = importlib.import_module(f"{runners.__name__}.{info.name}")
            registry._register(module, path)
        except RunnerPluginError:
            raise
        except Exception as exc:
            raise RunnerPluginError(f"{path}: {type(exc).__name__}: {exc}") from exc
    directory = plugin_dir if plugin_dir is not None else Path.home() / ".config" / "aisema" / "runners"
    try:
        paths = sorted(directory.iterdir())
    except FileNotFoundError:
        return registry
    except OSError as exc:
        raise RunnerPluginError(f"{directory}: cannot read plugin directory: {exc}") from exc
    for path in paths:
        if path.suffix != ".py" or path.stem.startswith("_"):
            continue
        try:
            if not path.is_file():
                continue
            registry._register(_external_module(path), path)
        except RunnerPluginError:
            raise
        except Exception as exc:
            raise RunnerPluginError(f"{path}: {type(exc).__name__}: {exc}") from exc
    return registry


_registry: RunnerRegistry | None = None


def get_registry() -> RunnerRegistry:
    global _registry
    if _registry is None:
        _registry = load_registry()
    return _registry
