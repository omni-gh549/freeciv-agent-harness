import copy
import json
import threading
import time
from collections import deque
from datetime import datetime, timezone


class EventLog:
    def __init__(self, capacity=4096, journal=None):
        self.items = deque(maxlen=capacity)
        self.cursor = 0
        self.changed = threading.Condition()
        self.journal = journal
        if journal:
            journal.parent.mkdir(parents=True, exist_ok=True)
            if journal.exists():
                for line in journal.read_text(encoding='utf-8').splitlines():
                    try:
                        event = json.loads(line)
                        self.cursor = max(self.cursor, event['sequence'])
                        self.items.append(event)
                    except (ValueError, KeyError, TypeError):
                        continue

    def emit(self, kind, **data):
        with self.changed:
            self.cursor += 1
            event = {'sequence': self.cursor, 'kind': kind,
                     'timestamp': datetime.now(timezone.utc).isoformat(), **data}
            self.items.append(event)
            if self.journal:
                with self.journal.open('a', encoding='utf-8') as f:
                    f.write(json.dumps(event, ensure_ascii=False) + '\n')
            self.changed.notify_all()
            return copy.deepcopy(event)

    def read(self, after=0, kinds=None, timeout=0, limit=100):
        if not isinstance(after, int) or after < 0 or not 0 <= timeout <= 55 or not 1 <= limit <= 1000:
            raise ValueError('Invalid event cursor, timeout (0..55), or limit (1..1000)')
        deadline = time.monotonic() + timeout
        with self.changed:
            while True:
                matches = [e for e in self.items if e['sequence'] > after and (not kinds or e['kind'] in kinds)]
                if matches or after > self.cursor or time.monotonic() >= deadline:
                    page = matches[:limit]
                    more = len(matches) > limit
                    cursor = page[-1]['sequence'] if more else self.cursor
                    oldest = self.items[0]['sequence'] if self.items else self.cursor + 1
                    return {'events': copy.deepcopy(page), 'cursor': cursor,
                            'has_more': more, 'truncated': after < oldest - 1 or after > self.cursor}
                self.changed.wait(max(0, deadline - time.monotonic()))
