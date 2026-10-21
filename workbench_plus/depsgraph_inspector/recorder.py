# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Records dependency graph evaluations: when they happened, how long they took and which data-blocks they updated.
"""

__all__ = (
    "Record",
    "Recorder",
)

import collections
import time
from typing import NamedTuple

RECORDS_MAX = 2000


class IdUpdate(NamedTuple):
    id_type: str
    name: str
    is_updated_transform: bool
    is_updated_geometry: bool
    is_updated_shading: bool

    @classmethod
    def from_depsgraph_update(cls, update):
        return cls(
            update.id.id_type,
            update.id.original.name,
            update.is_updated_transform,
            update.is_updated_geometry,
            update.is_updated_shading,
        )

    def as_dict(self):
        return self._asdict()


class Record(NamedTuple):
    kind: str
    """``'FRAME'`` for a frame change, ``'UPDATE'`` for an evaluation of other changes."""
    frame: int
    time_start: float
    """Seconds since the recorder was created or cleared."""
    duration: float
    """Seconds."""
    updates: tuple

    @property
    def label(self):
        return "Frame {:d}".format(self.frame) if self.kind == 'FRAME' else "Update"

    def as_dict(self):
        return {
            "kind": self.kind,
            "frame": self.frame,
            "time_start": self.time_start,
            "duration": self.duration,
            "updates": [update.as_dict() for update in self.updates],
        }


class Recorder:
    """
    Pairs the ``*_pre`` and ``*_post`` handler calls of each evaluation into a :class:`Record`.

    Only the latest :data:`RECORDS_MAX` records are kept.
    """

    def __init__(self, limit=RECORDS_MAX):
        self.records = collections.deque(maxlen=limit)
        self._time_origin = time.perf_counter()
        self._time_begin = {}

    def clear(self):
        self.records.clear()
        self._time_origin = time.perf_counter()
        self._time_begin.clear()

    def begin(self, kind):
        self._time_begin[kind] = time.perf_counter()

    def end(self, kind, frame, depsgraph):
        time_end = time.perf_counter()
        time_begin = self._time_begin.pop(kind, None)
        if time_begin is None:
            # Recording started between the `pre` and `post` handlers.
            return None
        updates = tuple(IdUpdate.from_depsgraph_update(update) for update in depsgraph.updates)
        record = Record(kind, frame, time_begin - self._time_origin, time_end - time_begin, updates)
        self.records.append(record)
        return record

    @property
    def last(self):
        return self.records[-1] if self.records else None

    def sorted_records(self, key='DURATION'):
        """Records sorted by ``'DURATION'`` (slowest first), ``'FRAME'`` or ``'ORDER'`` (as recorded)."""
        if key == 'DURATION':
            return sorted(self.records, key=lambda record: record.duration, reverse=True)
        if key == 'FRAME':
            return sorted(self.records, key=lambda record: (record.frame, record.time_start))
        return list(self.records)

    def id_costs(self):
        """
        ``[(id_type, name, count, seconds), ...]``: how many recorded evaluations updated each data-block
        and their total duration, most expensive first.

        Evaluations update several data-blocks at once, so this ranks suspects, it does not split the time.
        """
        costs = {}
        for record in self.records:
            for update in record.updates:
                key = (update.id_type, update.name)
                count, seconds = costs.get(key, (0, 0.0))
                costs[key] = (count + 1, seconds + record.duration)
        return sorted(
            ((id_type, name, count, seconds) for (id_type, name), (count, seconds) in costs.items()),
            key=lambda item: (-item[3], item[0], item[1]),
        )

    def as_dict(self):
        return {"version": 1, "records": [record.as_dict() for record in self.records]}

    def as_chrome_trace(self):
        """The records in the Chrome trace event format, which Perfetto and ``chrome://tracing`` open."""
        events = [{
            "name": "process_name", "ph": "M", "pid": 1, "tid": 1, "args": {"name": "Blender depsgraph"},
        }]
        for record in self.records:
            events.append({
                "name": record.label,
                "cat": "depsgraph",
                "ph": "X",
                "ts": round(record.time_start * 1e6, 3),
                "dur": round(record.duration * 1e6, 3),
                "pid": 1,
                "tid": 1 if record.kind == 'FRAME' else 2,
                "args": {
                    "frame": record.frame,
                    "updated": ["{:s}:{:s}".format(update.id_type, update.name) for update in record.updates],
                },
            })
        return {"traceEvents": events, "displayTimeUnit": "ms"}
