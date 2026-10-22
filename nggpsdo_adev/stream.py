# Copyright (c) 2026 Viewstreet Technologies
# SPDX-License-Identifier: MIT

"""Bounded SSE decoding and reconnecting NGGPSDO transport."""
import json
import queue
import threading
import urllib.parse
import urllib.request

MAX_LINE = 65536


def parameter_url(value, event_type="fc", after_id="Live only", interval_ms="1000"):
    """Build firmware-supported decimal query parameters from UI selections."""
    def decimal(value, maximum, label, minimum=0):
        value = str(value).strip()
        if not value.isascii() or not value.isdecimal() or not minimum <= int(value) <= maximum:
            raise ValueError(f"{label} must be an integer from {minimum} to {maximum}")
        return str(int(value))

    event_type = str(event_type).strip()
    if event_type.isascii() and event_type.isdecimal():
        event_type = decimal(event_type, 65535, "Event type", 1)
    elif (not 1 <= len(event_type) <= 40 or not event_type.isascii()
          or any(not (c.isalnum() or c in "-_.") for c in event_type)):
        raise ValueError("Event type must be a name of 1–40 letters, digits, '-', '_' or '.'")
    interval_ms = decimal(interval_ms, 2147483647, "Replay interval", 1)
    parts = urllib.parse.urlsplit(endpoint(value))
    query = dict(urllib.parse.parse_qsl(parts.query))
    query["eventType"] = event_type
    query.pop("afterEventId", None)
    query.pop("replayIntervalMs", None)
    if after_id.strip() != "Live only":
        query["afterEventId"] = decimal(after_id, 18446744073709551615, "After event ID")
        query["replayIntervalMs"] = interval_ms
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def events(response):
    data, name, event_id = [], "message", ""
    size = 0
    while True:
        raw = response.readline(MAX_LINE + 1)
        if not raw:
            return
        if len(raw) > MAX_LINE:
            raise ValueError("SSE line exceeds limit")
        line = raw.decode("utf-8-sig").rstrip("\r\n")
        if not line:
            if data:
                yield name, event_id, "\n".join(data)
            data, name, event_id, size = [], "message", "", 0
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "data":
            size += len(value)
            if size > MAX_LINE:
                raise ValueError("SSE event exceeds limit")
            data.append(value)
        elif field == "event":
            name = value
        elif field == "id" and "\0" not in value:
            event_id = value


def endpoint(value, replay=False):
    if "://" not in value:
        value = "http://" + value
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Enter an HTTP device address")
    query = dict(urllib.parse.parse_qsl(parts.query))
    query.setdefault("eventType", "fc")
    if replay:
        query["afterEventId"] = "0"
        query["replayIntervalMs"] = "1"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc,
        parts.path if parts.path not in ("", "/") else "/events",
        urllib.parse.urlencode(query), ""))


class Client(threading.Thread):
    def __init__(self, url, output):
        super().__init__(daemon=True)
        self.url, self.output = url, output
        self.stopped = threading.Event()
        self.cursor = ""

    def emit(self, item):
        while not self.stopped.is_set():
            try:
                self.output.put(item, timeout=.2)
                return
            except queue.Full:
                pass

    def run(self):
        while not self.stopped.is_set():
            try:
                self.emit(("status", "Connecting…"))
                headers = {"Accept": "text/event-stream"}
                if self.cursor:
                    headers["Last-Event-ID"] = self.cursor
                request = urllib.request.Request(self.url, headers=headers)
                with urllib.request.urlopen(request, timeout=10) as response:
                    if response.headers.get_content_type() != "text/event-stream":
                        raise ValueError("Device did not return an SSE stream")
                    self.emit(("status", "Connected"))
                    for name, event_id, payload in events(response):
                        if self.stopped.is_set():
                            return
                        if name != "fc":
                            continue
                        try:
                            data = json.loads(payload)
                            from .analysis import Sample
                            sample = Sample.parse(data, event_id)
                        except (ValueError, KeyError, TypeError, OverflowError) as error:
                            self.emit(("status", f"Skipped malformed event: {error}"))
                            continue
                        self.emit(("sample", sample))
                        if event_id:
                            self.cursor = event_id
                raise ConnectionError("Stream ended")
            except Exception as error:
                self.emit(("status", f"Reconnecting in 3 s: {error}"))
                if self.stopped.wait(3):
                    return

    def stop(self):
        self.stopped.set()
