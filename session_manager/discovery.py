from __future__ import annotations

from typing import Iterable, Optional

from .model import SessionEntry
from .runner_registry import get_registry


def available_harnesses() -> tuple[str, ...]:
    """Return registered identities available without starting their harnesses."""
    return tuple(runner.name for runner in get_registry().all() if runner.is_available())


def refresh_last_used(entries: Iterable[SessionEntry]) -> None:
    latest: dict[tuple[str, str], int] = {}
    for runner in get_registry().all():
        if not runner.is_available():
            continue
        for item in runner.discover():
            key = (item.runner, item.session_id)
            latest[key] = max(latest.get(key, 0), item.updated_ns)
    for entry in entries:
        entry.last_used_ns = latest.get((entry.runner, entry.session_id), 0)


def discover_recent(
    selector: int | str,
    count: Optional[int] = None,
    runners: Optional[Iterable[str]] = None,
) -> list[SessionEntry]:
    """Discover globally newest sessions, preserving legacy runner/count use."""
    registry = get_registry()
    if count is not None:
        runner = str(selector)
        if registry.get(runner) is None:
            raise ValueError(f"unsupported harness: {runner}")
        limit: Optional[int] = count
        selected_runners = (runner,)
    else:
        if isinstance(selector, str) and selector.lower() == "all":
            limit = None
        else:
            try:
                limit = int(selector)
            except (TypeError, ValueError) as exc:
                raise ValueError("session count must be a positive integer") from exc
        selected_runners = tuple(runners) if runners is not None else available_harnesses()
    if limit is not None and limit < 1:
        raise ValueError("session count must be a positive integer")

    metadata = []
    for name in selected_runners:
        runner_instance = registry.get(name)
        if runner_instance is not None:
            metadata.extend(runner_instance.discover())
    metadata.sort(key=lambda item: (item.updated_ns, item.runner, item.session_id), reverse=True)

    result: list[SessionEntry] = []
    selected_metadata = metadata if limit is None else metadata[:limit]
    for item in selected_metadata:
        command = item.resume_command
        result.append(
            SessionEntry(
                raw=f"{command} {item.description}",
                command=command,
                separator=" ",
                description=item.description,
                line_number=0,
                source="discovered",
                last_used_ns=item.updated_ns,
                user_queries=list(item.user_queries),
                inspection_items=list(item.inspection_items),
            )
        )
    return result
