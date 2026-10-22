# Copyright (c) 2026 Viewstreet Technologies
# SPDX-License-Identifier: MIT

import io
import math
import unittest
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import build_opener, ProxyHandler

from nggpsdo_adev.analysis import Analysis, Sample
from nggpsdo_adev.stream import Client, endpoint, events, parameter_url


def sample(i, value=0, flags=7, timestamp=None):
    return Sample(i, timestamp if timestamp is not None else 100000+i*4000,
                  4000000, 1e7 + value, 1e7, flags)


class CoreTests(unittest.TestCase):
    def test_axis_labels_use_decimal_numbers(self):
        from nggpsdo_adev.app import axis_number
        for value, expected in [(1e-12, '0.000000000001'), (1e5, '100000'),
                                (-2.5e-7, '-0.00000025'), (2.48, '2.48'),
                                (-0.0, '0'), (1.2e7, '12000000')]:
            self.assertEqual(axis_number(value), expected)


    def test_controller_measurement_is_optional_and_independent(self):
        payload = dict(sampleNumber='1', endTimeUnixMs=104000, intervalUs=4000000,
                       frequencyHz=10000000.03, nominalHz=10000000, flags=263,
                       controllerFrequencyHz=10000000.0001, controllerFilterLevel=5)
        parsed = Sample.parse(payload)
        self.assertEqual(parsed.controller_frequency, 10000000.0001)
        self.assertEqual(parsed.controller_level, 5)
        self.assertEqual(parsed.frequency, payload['frequencyHz'])
        first, second = Analysis(), Analysis()
        from dataclasses import replace
        for i in range(20):
            raw = sample(i, math.sin(i)*.02)
            first.add(raw)
            second.add(replace(raw, controller_frequency=1e7, controller_level=5))
        for kind in ('ADEV', 'MDEV', 'HDEV'):
            self.assertEqual(first.curve(kind), second.curve(kind))
        for field, value in [('flags', 7), ('controllerFrequencyHz', None),
                             ('controllerFrequencyHz', 'nan'), ('controllerFrequencyHz', 0),
                             ('controllerFilterLevel', -1)]:
            self.assertIsNone(Sample.parse(dict(payload, **{field: value})).controller_frequency)
        self.assertIsNone(Sample.parse(dict(payload, flags=7)).controller_level)

    def test_optional_ocxo_telemetry(self):
        payload = dict(sampleNumber='1', endTimeUnixMs=104000, intervalUs=4000000,
                       frequencyHz=10000000, nominalHz=10000000, flags=199,
                       ocxoTemperatureC=42.5, controlVoltageV=2.5)
        parsed = Sample.parse(payload)
        self.assertEqual(parsed.temperature_c, 42.5)
        self.assertEqual(parsed.control_voltage_v, 2.5)
        payload['flags'] = 7
        self.assertIsNone(Sample.parse(payload).temperature_c)
        payload.pop('ocxoTemperatureC')
        payload.pop('controlVoltageV')
        self.assertIsNone(Sample.parse(payload).control_voltage_v)
        payload.update(flags=199, ocxoTemperatureC=None, controlVoltageV='')
        self.assertIsNone(Sample.parse(payload).temperature_c)
        self.assertIsNone(Sample.parse(payload).control_voltage_v)

    def test_stability_against_direct_phase_sums(self):
        values = [3, -2, 7, 1, -4, 2, 8, -1, 5, 0, 4, -3, 9, 2, -5, 6]
        a = Analysis()
        phase = [0.0]
        for i, value in enumerate(values):
            a.add(sample(i, value))
            phase.append(phase[-1] + value / 1e7)
        for kind in ('MDEV', 'HDEV'):
            for tau, result, count in a.curve(kind):
                m = int(tau/4)
                if kind == 'MDEV':
                    terms = [sum(phase[j+k+2*m] - 2*phase[j+k+m] + phase[j+k]
                                 for k in range(m)) / (m*m)
                             for j in range(len(phase)-3*m+1)]
                    divisor = 2
                else:
                    terms = [(phase[j+3*m] - 3*phase[j+2*m]
                              + 3*phase[j+m] - phase[j]) / m
                             for j in range(len(phase)-3*m)]
                    divisor = 6
                expected = math.sqrt(sum(x*x for x in terms)/(divisor*len(terms)))
                self.assertEqual(count, len(terms))
                self.assertTrue(math.isclose(result, expected, rel_tol=1e-12, abs_tol=1e-20))

    def test_hadamard_rejects_linear_drift(self):
        a = Analysis()
        for i in range(32):
            a.add(sample(i, i))
        for _, value, _ in a.curve('HDEV'):
            self.assertLess(value, 1e-18)
        self.assertGreater(a.curve()[0][1], 0)

    def test_new_statistics_do_not_bridge_gaps(self):
        a = Analysis()
        for i in (1, 2, 4, 5):
            a.add(sample(i, i))
        self.assertEqual(a.curve('HDEV'), [])
        self.assertEqual(a.curve('MDEV')[0][2], 2)
        self.assertEqual(len(a.curve('MDEV')), 1)

    def test_constant(self):
        a = Analysis()
        for i in range(8):
            a.add(sample(i))
        self.assertEqual(a.curve(), [(4.0, 0.0, 7), (8.0, 0.0, 5), (16.0, 0.0, 1)])

    def test_known_alternating_frequency(self):
        a = Analysis()
        for i in range(8):
            a.add(sample(i, (-1)**i))
        self.assertAlmostEqual(a.curve()[0][1], math.sqrt(2)*1e-7)
        self.assertEqual(a.curve()[1][1], 0)

    def test_gaps_never_bridge(self):
        a = Analysis()
        for i in (1, 2, 4, 5):
            a.add(sample(i, i))
        self.assertEqual(len(a.curve()), 1)
        self.assertEqual(a.curve()[0][2], 2)

    def test_invalid_splits_run(self):
        a = Analysis()
        a.add(sample(1))
        a.add(sample(2, flags=0))
        a.add(sample(3))
        self.assertEqual(a.curve(), [])
        self.assertEqual(a.rejected, 1)

    def test_discontinuity_and_time_gap(self):
        for changed in (sample(2, flags=15), sample(2, timestamp=999999)):
            a = Analysis()
            a.add(sample(1))
            a.add(changed)
            self.assertEqual(a.curve(), [])

    def test_outlier_and_rolling_window(self):
        a = Analysis(limit=4)
        self.assertFalse(a.add(sample(0, value=2000)))
        for i in range(10):
            a.add(sample(i))
        self.assertEqual(a.curve()[0][2], 3)

    def test_sse_multiline_and_comments(self):
        raw = io.BytesIO(b': heartbeat\r\nevent: fc\r\nid: 42\r\ndata: {"x":\r\ndata: 1}\r\n\r\n')
        self.assertEqual(list(events(raw)), [('fc', '42', '{"x":\n1}')])

    def test_sse_bounds(self):
        with self.assertRaises(ValueError):
            list(events(io.BytesIO(b'data:' + b'x'*65536)))

    def test_endpoint(self):
        self.assertEqual(endpoint('192.168.1.4'), 'http://192.168.1.4/events?eventType=fc')
        self.assertIn('afterEventId=0', endpoint('http://device', True))
        with self.assertRaises(ValueError):
            endpoint('ftp://device')

    def test_named_event_url(self):
        self.assertIn('eventType=fc', parameter_url('device'))
        self.assertIn('eventType=1', parameter_url('device', '1'))
        self.assertIn('eventType=frequency.test-1_a', parameter_url('device', 'frequency.test-1_a'))
        for invalid in ('', 'x'*41, 'fc/name', 'fc&afterEventId=1', '65536'):
            with self.assertRaises(ValueError):
                parameter_url('device', invalid)

    def test_http_client_receives_fc_sample(self):
        payload = dict(sampleNumber='1', endTimeUnixMs=104000,
                       intervalUs=4000000, frequencyHz=10000000,
                       nominalHz=10000000, flags=7)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(('event: fc\nid: 99\ndata: '
                                  + json.dumps(payload) + '\n\n').encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        output = queue.Queue()
        client = Client(endpoint(f'127.0.0.1:{server.server_port}'), output)
        try:
            # Local test traffic must bypass workstation proxy settings.
            with patch('urllib.request.urlopen', build_opener(ProxyHandler({})).open):
                client.start()
                for _ in range(6):
                    kind, value = output.get(timeout=5)
                    if kind == 'status' and 'WinError 10013' in value:
                        self.skipTest('Execution sandbox forbids local client sockets')
                    if kind == 'sample':
                        self.assertEqual(value.event_id, '99')
                        self.assertEqual(value.sequence, 1)
                        break
                else:
                    self.fail('No sample received')
        finally:
            client.stop()
            client.join(timeout=5)
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
