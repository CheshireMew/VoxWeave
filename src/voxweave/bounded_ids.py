from __future__ import annotations

from collections import deque


class BoundedIdSet:
    """Insertion-ordered membership window for already projected task events."""

    def __init__(self, capacity: int = 2048) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._order: deque[str] = deque()
        self._values: set[str] = set()

    def __contains__(self, value: object) -> bool:
        return value in self._values

    def add(self, value: str) -> None:
        if value in self._values:
            return
        self._values.add(value)
        self._order.append(value)
        while len(self._order) > self.capacity:
            self._values.discard(self._order.popleft())

    def clear(self) -> None:
        self._order.clear()
        self._values.clear()
