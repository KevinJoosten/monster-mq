#!/usr/bin/env python3
"""
4-hop MQTT Round-Trip Benchmark  (stdlib only — no paho-mqtt needed)
=====================================================================
Mimics the MQTT → (service) → MQTT flow with 4 measurement taps:

  TAP1         TAP2         TAP3           TAP4
  Machine  ──► Transformer ──► Service ──► Transformer ──► Machine
  publish       receive        receive       receive          receive
  machine/u     machine/u      ons/u         ons/resp/u       mach/resp/u

All roles run in the same process (different MQTT client IDs).
Timestamps are embedded in the JSON payload and accumulated at each hop
so all 5 time points (t1..t5) are available when the final message arrives.

Usage
-----
  python3 rtt_benchmark.py
  python3 rtt_benchmark.py --monster-port 1883 --mosquitto-port 1884 \\
      --username Test --password Test --messages 100 --interval-ms 200
  python3 rtt_benchmark.py --skip-mosquitto
  python3 rtt_benchmark.py --skip-monster

Requirements: Python 3.7+, stdlib only, docker CLI on PATH for CPU stats
"""

import argparse
import json
import socket
import statistics
import struct
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Minimal MQTT 3.1.1 client — stdlib only
# Supports CONNECT, SUBSCRIBE, PUBLISH (QoS 0/1), DISCONNECT
# ---------------------------------------------------------------------------

def _varlen_encode(n):
    out = []
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            b |= 0x80
        out.append(b)
        if not n:
            break
    return bytes(out)


def _enc_str(s):
    b = s.encode('utf-8')
    return struct.pack('!H', len(b)) + b


class MqttClient:
    """Minimal MQTT 3.1.1 client using raw TCP sockets."""

    def __init__(self, client_id, host, port, username='', password=''):
        self.host = host
        self.port = port
        self._id = client_id
        self._user = username
        self._pass = password
        self._sock = None
        self._pid = 0
        self._pid_lock = threading.Lock()
        self._wlock = threading.Lock()
        self._connack = threading.Event()
        self._subacks = {}          # pid -> Event
        self.on_message: Optional[Callable[[str, bytes], None]] = None
        self._running = False
        self._buf = b''
        self._sock: Optional[socket.socket] = None

    # ── packet id ────────────────────────────────────────────────
    def _next_pid(self):
        with self._pid_lock:
            self._pid = (self._pid % 65535) + 1
            return self._pid

    # ── send ─────────────────────────────────────────────────────
    def _send(self, data):
        with self._wlock:
            self._sock.sendall(data)  # type: ignore[union-attr]

    def _pkt(self, first_byte, body):
        return bytes([first_byte]) + _varlen_encode(len(body)) + body

    # ── CONNECT ──────────────────────────────────────────────────
    def connect(self, timeout=10):
        self._sock = socket.create_connection((self.host, self.port), timeout=10)
        self._sock.settimeout(None)

        flags = 0x02  # clean session
        payload = _enc_str(self._id)
        if self._user:
            flags |= 0x80
            payload += _enc_str(self._user)
        if self._pass:
            flags |= 0x40
            payload += _enc_str(self._pass)

        vh = _enc_str('MQTT') + bytes([4, flags]) + struct.pack('!H', 60)
        self._send(self._pkt(0x10, vh + payload))

        self._running = True
        threading.Thread(target=self._recv_loop, daemon=True).start()
        if not self._connack.wait(timeout):
            raise TimeoutError(f'CONNACK timeout connecting to {self.host}:{self.port}')

    # ── SUBSCRIBE ────────────────────────────────────────────────
    def subscribe(self, topic, qos=1, timeout=5):
        pid = self._next_pid()
        ev = threading.Event()
        self._subacks[pid] = ev
        body = struct.pack('!H', pid) + _enc_str(topic) + bytes([qos])
        self._send(self._pkt(0x82, body))
        if not ev.wait(timeout):
            raise TimeoutError(f'SUBACK timeout for {topic}')

    # ── PUBLISH ──────────────────────────────────────────────────
    def publish(self, topic, payload, qos=1):
        if isinstance(payload, str):
            payload = payload.encode('utf-8')
        body = _enc_str(topic)
        if qos == 0:
            self._send(self._pkt(0x30, body + payload))
        else:
            pid = self._next_pid()
            self._send(self._pkt(0x32, body + struct.pack('!H', pid) + payload))

    # ── DISCONNECT ───────────────────────────────────────────────
    def disconnect(self):
        self._running = False
        try:
            self._send(bytes([0xE0, 0x00]))
        except Exception:
            pass
        try:
            self._sock.close()  # type: ignore[union-attr]
        except Exception:
            pass

    # ── receive loop ─────────────────────────────────────────────
    def _read_exact(self, n):
        while len(self._buf) < n:
            chunk = self._sock.recv(4096)  # type: ignore[union-attr]
            if not chunk:
                raise ConnectionError('Socket closed')
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_varlen(self):
        mult, val = 1, 0
        while True:
            b = self._read_exact(1)[0]
            val += (b & 0x7F) * mult
            mult <<= 7
            if not (b & 0x80):
                return val

    def _recv_loop(self):
        while self._running:
            try:
                hdr = self._read_exact(1)[0]
                ptype = (hdr >> 4) & 0x0F
                pflags = hdr & 0x0F
                rlen = self._read_varlen()
                data = self._read_exact(rlen) if rlen else b''

                if ptype == 2:    # CONNACK
                    if data[1] == 0:
                        self._connack.set()

                elif ptype == 9:  # SUBACK
                    pid = struct.unpack('!H', data[:2])[0]
                    ev = self._subacks.pop(pid, None)
                    if ev:
                        ev.set()

                elif ptype == 4:  # PUBACK — not tracked for benchmark
                    pass

                elif ptype == 3:  # PUBLISH
                    qos = (pflags >> 1) & 0x03
                    tlen = struct.unpack('!H', data[:2])[0]
                    topic = data[2:2 + tlen].decode('utf-8')
                    off = 2 + tlen
                    if qos > 0:
                        pid = struct.unpack('!H', data[off:off + 2])[0]
                        off += 2
                        self._send(bytes([0x40, 0x02]) + struct.pack('!H', pid))
                    msg = data[off:]
                    if self.on_message:
                        try:
                            self.on_message(topic, msg)
                        except Exception:
                            pass

            except Exception:
                break


# ---------------------------------------------------------------------------
# Topic templates
# ---------------------------------------------------------------------------
UNIT = "bench_unit_01"
T_MACHINE      = f"machine/{UNIT}"
T_ONS          = f"ons/{UNIT}"
T_ONS_RESP     = f"ons/response/{UNIT}"
T_MACHINE_RESP = f"machine/response/{UNIT}"

QOS = 1
QOS_BURST = 0  # QoS 0 for burst mode — no PUBACKs, no queue writes, pure throughput


# ---------------------------------------------------------------------------
# CPU sampler (docker stats)
# ---------------------------------------------------------------------------

class CpuSampler:
    """Polls 'docker stats --no-stream' in a background thread."""

    def __init__(self, container: str, interval_s: float = 1.0):
        self.container = container
        self.interval_s = interval_s
        self._samples: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._stop.clear()
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self):
        while not self._stop.is_set():
            try:
                out = subprocess.check_output(
                    ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", self.container],
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                ).decode().strip()
                pct = float(out.replace("%", ""))
                self._samples.append(pct)
            except Exception:
                pass
            self._stop.wait(self.interval_s)

    def summary(self) -> dict:
        s = self._samples
        if not s:
            return {"samples": 0, "available": False}
        return {
            "available": True,
            "samples": len(s),
            "avg_%":    round(statistics.mean(s), 2),
            "max_%":    round(max(s), 2),
            "min_%":    round(min(s), 2),
            "p95_%":    round(statistics.quantiles(s, n=20)[18] if len(s) >= 20 else max(s), 2),
        }


# ---------------------------------------------------------------------------
# Per-result record
# ---------------------------------------------------------------------------

@dataclass
class HopResult:
    seq:      int
    t1: float = 0.0   # Machine publish
    t2: float = 0.0   # Transformer receives machine/{unit}
    t3: float = 0.0   # Service receives ons/{unit}
    t4: float = 0.0   # Transformer receives ons/response/{unit}
    t5: float = 0.0   # Machine receives machine/response/{unit}

    @property
    def hop1(self):  return (self.t2 - self.t1) * 1000   # sent → broker → transformer
    @property
    def hop2(self):  return (self.t3 - self.t2) * 1000   # transformer repost → service
    @property
    def hop3(self):  return (self.t4 - self.t3) * 1000   # service response → transformer
    @property
    def hop4(self):  return (self.t5 - self.t4) * 1000   # transformer repost → machine
    @property
    def total(self): return (self.t5 - self.t1) * 1000


# ---------------------------------------------------------------------------
# Main benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(
    label: str,
    host: str,
    port: int,
    container: str,
    username: str,
    password: str,
    n_messages: int,
    interval_s: float,
) -> dict:
    uid = uuid.uuid4().hex[:8]
    burst = interval_s == 0
    qos = QOS_BURST if burst else QOS
    results = {}
    lock = threading.Lock()
    done_event = threading.Event()

    c_machine    = MqttClient(f"bench_machine_{uid}",    host, port, username, password)
    c_transformer= MqttClient(f"bench_transformer_{uid}",host, port, username, password)
    c_service    = MqttClient(f"bench_service_{uid}",    host, port, username, password)

    # -- Role: Machine (publishes + receives final response) ------------------
    def on_machine_msg(topic, payload):
        t5 = time.perf_counter()
        try:
            data = json.loads(payload)
            seq = data["seq"]
            with lock:
                if seq in results:
                    results[seq].t5 = t5
                    if sum(1 for r in results.values() if r.t5 > 0) >= n_messages:
                        done_event.set()
        except Exception:
            pass

    c_machine.on_message = on_machine_msg

    # -- Role: Transformer (forwards machine→ons and ons/resp→machine/resp) --
    def on_transformer_msg(topic, payload):
        t_now = time.perf_counter()
        try:
            data = json.loads(payload)
            seq = data["seq"]
            if topic == T_MACHINE:
                with lock:
                    if seq not in results:
                        results[seq] = HopResult(seq=seq, t1=data["t1"])
                    results[seq].t2 = t_now
                data["t2"] = t_now
                c_transformer.publish(T_ONS, json.dumps(data), qos=qos)
            elif topic.startswith("ons/response/"):
                with lock:
                    if seq in results:
                        results[seq].t4 = t_now
                data["t4"] = t_now
                c_transformer.publish(T_MACHINE_RESP, json.dumps(data), qos=qos)
        except Exception:
            pass

    c_transformer.on_message = on_transformer_msg

    # -- Role: Service (receives ons/{unit}, replies to ons/response/{unit}) --
    def on_service_msg(topic, payload):
        t_now = time.perf_counter()
        try:
            data = json.loads(payload)
            seq = data["seq"]
            with lock:
                if seq in results:
                    results[seq].t3 = t_now
            data["t3"] = t_now
            c_service.publish(T_ONS_RESP, json.dumps(data), qos=qos)
        except Exception:
            pass

    c_service.on_message = on_service_msg

    # -- Connect all clients --------------------------------------------------
    print(f"\n[{label}] Connecting to {host}:{port} ...")
    c_machine.connect()
    c_transformer.connect()
    c_service.connect()

    # -- Subscribe ------------------------------------------------------------
    c_machine.subscribe(T_MACHINE_RESP, qos=qos)
    c_transformer.subscribe(T_MACHINE, qos=qos)
    c_transformer.subscribe("ons/response/#", qos=qos)
    c_service.subscribe(T_ONS, qos=qos)

    time.sleep(0.5)  # let subscriptions propagate

    # -- Start CPU sampler ----------------------------------------------------
    sampler = CpuSampler(container, interval_s=1.0)
    sampler.start()

    # -- Publish messages -----------------------------------------------------
    if interval_s > 0:
        print(f"[{label}] Sending {n_messages} messages at {1/interval_s:.1f} msg/s ...")
        for seq in range(n_messages):
            t1 = time.perf_counter()
            payload = json.dumps({"seq": seq, "t1": t1})
            with lock:
                results[seq] = HopResult(seq=seq, t1=t1)
            c_machine.publish(T_MACHINE, payload, qos=qos)
            time.sleep(interval_s)
    else:
        # Burst mode: fire all messages as fast as possible
        print(f"[{label}] BURST: sending {n_messages} messages as fast as possible ...")
        burst_start = time.perf_counter()
        for seq in range(n_messages):
            t1 = time.perf_counter()
            payload = json.dumps({"seq": seq, "t1": t1})
            with lock:
                results[seq] = HopResult(seq=seq, t1=t1)
            c_machine.publish(T_MACHINE, payload, qos=qos)
        burst_end = time.perf_counter()
        burst_send_s = burst_end - burst_start
        print(f"[{label}] Burst send complete: {n_messages} msgs in {burst_send_s*1000:.0f}ms = {n_messages/burst_send_s:.0f} msg/s publish rate")

    # -- Wait for all responses (burst needs longer timeout) -----------------
    wait_timeout = 120.0 if interval_s == 0 else 30.0
    done_event.wait(timeout=wait_timeout)
    time.sleep(0.5)

    sampler.stop()
    c_machine.disconnect()
    c_transformer.disconnect()
    c_service.disconnect()

    # -- Compute stats --------------------------------------------------------
    complete   = [r for r in results.values() if r.t5 > 0]
    incomplete = [r for r in results.values() if r.t5 == 0]

    # Where did incomplete messages last succeed?
    # t2=0 → never reached transformer (lost: machine→broker→transformer)
    # t3=0 → transformer got it but service never received (lost: ons publish)
    # t4=0 → service got it but transformer never got response (lost: ons/response)
    # t5=0 → transformer got response but machine never received final (lost: machine/response)
    lost_at = {
        "TAP1_never_reached_transformer": sum(1 for r in incomplete if r.t2 == 0),
        "TAP2_never_reached_service":     sum(1 for r in incomplete if r.t2 > 0 and r.t3 == 0),
        "TAP3_never_reached_transformer": sum(1 for r in incomplete if r.t3 > 0 and r.t4 == 0),
        "TAP4_never_reached_machine":     sum(1 for r in incomplete if r.t4 > 0 and r.t5 == 0),
    }

    # Throughput: from first t1 to last t5 among completed messages
    throughput_msgps = None
    if complete:
        t_first = min(r.t1 for r in complete)
        t_last  = max(r.t5 for r in complete)
        elapsed = t_last - t_first
        if elapsed > 0:
            throughput_msgps = round(len(complete) / elapsed, 1)

    def stats(values):
        if not values:
            return {}
        s = sorted(values)
        return {
            "min":  round(min(s), 2),
            "avg":  round(statistics.mean(s), 2),
            "p50":  round(statistics.median(s), 2),
            "p95":  round(s[int(len(s) * 0.95)], 2),
            "p99":  round(s[int(len(s) * 0.99)], 2),
            "max":  round(max(s), 2),
        }

    return {
        "label":      label,
        "sent":       n_messages,
        "received":   len(complete),
        "lost":       len(incomplete),
        "lost_at":    lost_at,
        "throughput_msgps": throughput_msgps,
        "burst":      burst,
        "qos":        qos,
        "hop1_sent_to_transformer_ms":      stats([r.hop1 for r in complete]),
        "hop2_transformer_to_service_ms":   stats([r.hop2 for r in complete]),
        "hop3_service_to_transformer_ms":   stats([r.hop3 for r in complete]),
        "hop4_transformer_to_machine_ms":   stats([r.hop4 for r in complete]),
        "total_rtt_ms":                     stats([r.total for r in complete]),
        "cpu": sampler.summary(),
    }


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def print_report(result: dict):
    label = result["label"]
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  {label}")
    print(sep)
    mode = "BURST QoS0" if result.get("burst") else f"5 msg/s QoS{result.get('qos',1)}"
    tput = f"  throughput={result['throughput_msgps']} msg/s" if result.get('throughput_msgps') else ""
    print(f"  Messages  sent={result['sent']}  received={result['received']}  lost={result['lost']}  mode={mode}{tput}")
    if result["lost"] > 0:
        la = result.get("lost_at", {})
        parts = []
        labels = {
            "TAP1_never_reached_transformer": "TAP1(broker→transformer)",
            "TAP2_never_reached_service":     "TAP2(transformer→service)",
            "TAP3_never_reached_transformer": "TAP3(service→transformer)",
            "TAP4_never_reached_machine":     "TAP4(transformer→machine)",
        }
        for k, label_str in labels.items():
            n = la.get(k, 0)
            if n > 0:
                parts.append(f"{label_str}={n}")
        if parts:
            print(f"  Lost at: {', '.join(parts)}")
    print()

    hop_keys = [
        ("hop1_sent_to_transformer_ms",    "TAP1  sent → transformer     "),
        ("hop2_transformer_to_service_ms", "TAP2  transformer → service  "),
        ("hop3_service_to_transformer_ms", "TAP3  service → transformer  "),
        ("hop4_transformer_to_machine_ms", "TAP4  transformer → machine  "),
        ("total_rtt_ms",                   "TOTAL RTT                    "),
    ]
    header = f"  {'Segment':<35} {'min':>7} {'avg':>7} {'p50':>7} {'p95':>7} {'p99':>7} {'max':>7}"
    print(header)
    print(f"  {'-'*35} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*7}")
    for key, label_str in hop_keys:
        s = result.get(key, {})
        if s:
            print(f"  {label_str:<35} {s['min']:>7.1f} {s['avg']:>7.1f} {s['p50']:>7.1f} {s['p95']:>7.1f} {s['p99']:>7.1f} {s['max']:>7.1f}  ms")

    cpu = result["cpu"]
    print()
    if cpu.get("available"):
        print(f"  CPU (docker stats, {cpu['samples']} samples)")
        print(f"    avg={cpu['avg_%']}%  max={cpu['max_%']}%  min={cpu['min_%']}%  p95={cpu['p95_%']}%")
        print(f"  Note: docker stats % is per-core (100% = 1 full core)")
    else:
        print("  CPU: docker stats unavailable (check container name / docker on PATH)")
    print()


def print_comparison(r1: dict, r2: dict):
    sep = "=" * 60
    print(f"\n{sep}")
    print("  COMPARISON")
    print(sep)

    a_label = r1["label"][:20]
    b_label = r2["label"][:20]
    print(f"  {'Segment':<35} {a_label:>12} {b_label:>12}  {'diff':>8}")
    print(f"  {'-'*35} {'-'*12} {'-'*12}  {'-'*8}")

    hop_keys = [
        ("hop1_sent_to_transformer_ms",    "TAP1  sent → transformer     "),
        ("hop2_transformer_to_service_ms", "TAP2  transformer → service  "),
        ("hop3_service_to_transformer_ms", "TAP3  service → transformer  "),
        ("hop4_transformer_to_machine_ms", "TAP4  transformer → machine  "),
        ("total_rtt_ms",                   "TOTAL RTT                    "),
    ]
    for key, label_str in hop_keys:
        a = r1.get(key, {}).get("avg", 0)
        b = r2.get(key, {}).get("avg", 0)
        diff = b - a
        sign = "+" if diff > 0 else ""
        print(f"  {label_str:<35} {a:>11.1f}ms {b:>11.1f}ms  {sign}{diff:>6.1f}ms")

    a_cpu = r1["cpu"].get("avg_%", "N/A")
    b_cpu = r2["cpu"].get("avg_%", "N/A")
    if isinstance(a_cpu, float) and isinstance(b_cpu, float):
        diff_cpu = b_cpu - a_cpu
        sign = "+" if diff_cpu > 0 else ""
        print(f"\n  {'CPU avg %':<35} {a_cpu:>11.1f}%  {b_cpu:>11.1f}%  {sign}{diff_cpu:>6.1f}%")
        print(f"  (100% = 1 core; divide by nproc for normalized load)")

    t1 = r1.get("throughput_msgps")
    t2 = r2.get("throughput_msgps")
    if t1 and t2:
        diff_t = t2 - t1
        sign = "+" if diff_t > 0 else ""
        print(f"  {'Throughput msg/s':<35} {t1:>11.1f}   {t2:>11.1f}   {sign}{diff_t:>6.1f}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="4-hop MQTT RTT benchmark: MonsterMQ vs Mosquitto")
    parser.add_argument("--host",             default="localhost")
    parser.add_argument("--monster-port",     type=int, default=1883)
    parser.add_argument("--mosquitto-port",   type=int, default=1884)
    parser.add_argument("--monster-container",  default="mqttBroker")
    parser.add_argument("--mosquitto-container", default="mqttTest")
    parser.add_argument("--username",         default="Test")
    parser.add_argument("--password",         default="Test")
    parser.add_argument("--messages",         type=int,   default=100)
    parser.add_argument("--interval-ms",      type=float, default=200,
                        help="ms between publishes (default 200 = 5 msg/s). Use --burst to ignore this.")
    parser.add_argument("--burst",            action="store_true",
                        help="Send all messages as fast as possible (ignores --interval-ms). Use --messages 10000.")
    parser.add_argument("--skip-monster",     action="store_true")
    parser.add_argument("--skip-mosquitto",   action="store_true")
    args = parser.parse_args()

    interval_s = 0.0 if args.burst else args.interval_ms / 1000.0
    results = []

    if not args.skip_monster:
        r = run_benchmark(
            label="MonsterMQ",
            host=args.host,
            port=args.monster_port,
            container=args.monster_container,
            username=args.username,
            password=args.password,
            n_messages=args.messages,
            interval_s=interval_s,
        )
        print_report(r)
        results.append(r)

    if not args.skip_mosquitto:
        r = run_benchmark(
            label="Mosquitto",
            host=args.host,
            port=args.mosquitto_port,
            container=args.mosquitto_container,
            username=args.username,
            password=args.password,
            n_messages=args.messages,
            interval_s=interval_s,
        )
        print_report(r)
        results.append(r)

    if len(results) == 2:
        print_comparison(results[0], results[1])


if __name__ == "__main__":
    main()
