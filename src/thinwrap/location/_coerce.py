"""Construction-time checks for the options dataclasses.

A value outside one of the library's closed vocabularies (``TravelMode``,
``TrafficMode``, ``PolylineQuality``, ``IsochroneType``, the ``include`` tokens) is
a bug in the calling code, not request data: the library translates these values
itself, so no vendor would ever see ``"flying"`` to reject it. Left unchecked,
each connector's translation falls through to its default branch and quietly
answers a different question (driving instead of flying, a distance isochrone
instead of a time one). So they raise the native exception at construction —
``ValueError`` for a string outside the set, ``TypeError`` for a non-string —
the same thing ``TravelMode("flying")`` raises, and deliberately NOT a
``ConnectorError``.

Valid raw strings (``"walking"``) keep working and are normalized to the enum
member, so connectors can compare against members without caring how the caller
spelled the value.
"""

from __future__ import annotations

import datetime as _dt
from enum import Enum
from typing import Any, Type


def _allowed(enum_cls: Type[Enum]) -> str:
    return ", ".join(repr(m.value) for m in enum_cls)


def coerce_enum(obj: Any, name: str, enum_cls: Type[Enum]) -> None:
    """Replace ``obj.<name>`` with its ``enum_cls`` member, or raise.

    Raises:
        TypeError: the value is not a string (``None`` included).
        ValueError: the string is not one of ``enum_cls``'s values.
    """
    value = getattr(obj, name)
    object.__setattr__(obj, name, _member(value, enum_cls, f"{type(obj).__name__}.{name}"))


def coerce_tokens(obj: Any, name: str, enum_cls: Type[Enum]) -> None:
    """Replace the ``obj.<name>`` token sequence with a tuple of ``enum_cls``
    members, or raise.

    A bare string is refused rather than iterated: ``include="name"`` would
    otherwise be read character by character.

    Raises:
        TypeError: the value is a string or not iterable, or an element is not a
            string.
        ValueError: an element is not one of ``enum_cls``'s values.
    """
    value = getattr(obj, name)
    label = f"{type(obj).__name__}.{name}"
    if isinstance(value, (str, bytes)) or not _iterable(value):
        raise TypeError(f"{label} must be a sequence of {enum_cls.__name__} tokens, got {type(value).__name__}")
    members = tuple(_member(v, enum_cls, f"{label}[{i}]") for i, v in enumerate(value))
    object.__setattr__(obj, name, members)


def check_departure_type(obj: Any, name: str = "departure_time") -> None:
    """Reject a ``departure_time`` that is neither a ``datetime`` nor a string.

    Strings are let through here because Esri accepts an ISO-8601 string (and the
    isochrone surface is string-typed); whether a string is acceptable, and
    whether it parses, is decided by the connector that formats it.

    Raises:
        TypeError: the value is not ``None``, a ``datetime`` or a ``str``.
    """
    value = getattr(obj, name)
    if value is None or isinstance(value, (_dt.datetime, str)):
        return
    raise TypeError(f"{type(obj).__name__}.{name} must be a datetime or an ISO-8601 string, got {type(value).__name__}")


def _member(value: Any, enum_cls: Type[Enum], label: str) -> Enum:
    if isinstance(value, enum_cls):
        return value
    if not isinstance(value, str):
        raise TypeError(
            f"{label} must be a {enum_cls.__name__} (one of {_allowed(enum_cls)}), got {type(value).__name__}"
        )
    try:
        return enum_cls(value)
    except ValueError:
        # `from None`: the enum's own lookup error says the same thing, less clearly.
        raise ValueError(f"{label}: unknown value {value!r}, expected one of {_allowed(enum_cls)}") from None


def _iterable(value: Any) -> bool:
    try:
        iter(value)
    except TypeError:
        return False
    return True
