# Copyright (c) 2026 Viewstreet Technologies
# SPDX-License-Identifier: MIT

"""Gap-aware overlapping Allan deviation of fractional frequency samples."""
import math
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    sequence: int
    timestamp: int
    interval: int
    frequency: float
    nominal: float
    flags: int
    event_id: str = ""
    temperature_c: float | None = None
    control_voltage_v: float | None = None
    controller_frequency: float | None = None
    controller_level: int | None = None

    @classmethod
    def parse(cls, data, event_id=""):
        def optional(name, mask):
            value = data.get(name)
            if value in (None, "") or not int(data["flags"]) & mask:
                return None
            value = float(value)
            return value if math.isfinite(value) else None

        sample = cls(int(data["sampleNumber"]), int(data["endTimeUnixMs"]),
                     int(data["intervalUs"]), float(data["frequencyHz"]),
                     float(data["nominalHz"]), int(data["flags"]), event_id,
                     optional("ocxoTemperatureC", 64), optional("controlVoltageV", 128))
        if int(data["flags"]) & 256 and data.get("controllerFrequencyHz") not in (None, ""):
            frequency = float(data["controllerFrequencyHz"])
            level = int(data.get("controllerFilterLevel") or 0)
            if math.isfinite(frequency) and frequency > 0 and level >= 0:
                from dataclasses import replace
                sample = replace(sample, controller_frequency=frequency, controller_level=level)
        if (sample.sequence < 0 or sample.timestamp < 0 or sample.interval <= 0
                or sample.nominal <= 0 or not math.isfinite(sample.nominal)
                or not math.isfinite(sample.frequency)):
            raise ValueError("Invalid measurement values")
        return sample


class Analysis:
    def __init__(self, limit=200000, max_ppm=100.0):
        self.samples = deque(maxlen=limit)
        self.max_ppm = max_ppm
        self.previous = None
        self.run = 0
        self.accepted = self.rejected = self.breaks = 0

    def add(self, sample):
        valid = ((sample.flags & 5) == 5 and not sample.flags & 48
                 and sample.timestamp > 0
                 and abs((sample.frequency - sample.nominal) / sample.nominal)
                 <= self.max_ppm * 1e-6)
        previous = self.previous
        continuous = (previous is not None and not sample.flags & 8
                      and sample.sequence == previous.sequence + 1
                      and sample.interval == previous.interval
                      and sample.nominal == previous.nominal
                      and abs(sample.timestamp - previous.timestamp
                              - sample.interval / 1000) <= 100)
        if not continuous or not valid:
            self.run += 1
            self.breaks += int(previous is not None)
        self.previous = sample if valid else None
        if valid:
            self.accepted += 1
            self.samples.append((self.run, sample))
        else:
            self.rejected += 1
        return valid

    def curve(self, kind="ADEV"):
        if kind not in ("ADEV", "MDEV", "HDEV"):
            raise ValueError("Unknown stability statistic")
        # Pool squared differences only within continuous runs sharing the
        # current gate/nominal frequency. Never compute across missing samples.
        if not self.samples:
            return []
        latest = self.samples[-1][1]
        groups = []
        last_run = None
        for run, sample in self.samples:
            if sample.interval != latest.interval or sample.nominal != latest.nominal:
                continue
            if run != last_run:
                groups.append([])
                last_run = run
            groups[-1].append((sample.frequency - sample.nominal) / sample.nominal)
        prefixes = []
        for group in groups:
            prefix = [0.0]
            for value in group:
                prefix.append(prefix[-1] + value)
            prefixes.append(prefix)
        points = []
        m = 1
        longest = max(map(len, groups))
        while 2 * m <= longest:
            total = 0.0
            pairs = 0
            for prefix in prefixes:
                n = len(prefix) - 1
                if kind == "HDEV":
                    for i in range(n - 3*m + 1):
                        delta = (prefix[i+3*m] - 3*prefix[i+2*m]
                                 + 3*prefix[i+m] - prefix[i]) / m
                        total += delta * delta
                        pairs += 1
                elif kind == "MDEV":
                    # Moving average of m adjacent Allan differences.
                    differences = [(prefix[i+2*m] - 2*prefix[i+m] + prefix[i]) / m
                                   for i in range(n - 2*m + 1)]
                    if len(differences) >= m:
                        summed = math.fsum(differences[:m])
                        total += (summed / m)**2
                        pairs += 1
                        for i in range(m, len(differences)):
                            summed += differences[i] - differences[i-m]
                            total += (summed / m)**2
                            pairs += 1
                else:
                    for i in range(n - 2*m + 1):
                        delta = (prefix[i+2*m] - 2*prefix[i+m] + prefix[i]) / m
                        total += delta * delta
                        pairs += 1
            if pairs:
                points.append((m * latest.interval / 1e6,
                               math.sqrt(total / ((6 if kind == "HDEV" else 2)*pairs)), pairs))
            m *= 2
        return points
