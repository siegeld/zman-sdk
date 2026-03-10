"""ZMAN SDK — Python client for Merging Technologies ZMAN/RAVENNA modules.

Controls ZMAN OEM modules (e.g. in Lyngdorf MP-60) via the CometD/Bayeux
HTTP API exposed on port 80 at /cometd.

Usage:
    from zman import ZMANClient

    client = ZMANClient("10.11.7.88")
    client.connect()
    print(client.get_identity())
    print(client.get_sinks())
    client.create_path(source="sap://MyStream", output_channels=[8, 9])
    client.close()
"""

__version__ = "1.0.0"

import json
import threading
import time

import requests


class ZMANClient:
    """Client for ZMAN RAVENNA module CometD API."""

    def __init__(self, host, port=80):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}/cometd"
        self.client_id = None
        self._msg_counter = 0
        self._state = {}  # full device state from push updates
        self._lock = threading.Lock()
        self._poll_thread = None
        self._stop = threading.Event()
        self._session = requests.Session()
        self._connected = threading.Event()
        self._query_results = []  # captured /ravenna/query results
        self._query_event = threading.Event()

    # ------------------------------------------------------------------
    # CometD transport
    # ------------------------------------------------------------------

    def _next_id(self):
        self._msg_counter += 1
        return f"sdk-{self._msg_counter}"

    def _post(self, messages):
        """Send CometD messages, return parsed response."""
        resp = self._session.post(
            self.base_url,
            json=messages,
            headers={"Content-Type": "application/json;charset=UTF8"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def _handshake(self):
        resp = self._post([{
            "version": "1.0",
            "minimumVersion": "0.9",
            "channel": "/meta/handshake",
            "supportedConnectionTypes": [],
            "id": self._next_id(),
        }])
        for msg in resp:
            if msg.get("channel") == "/meta/handshake" and msg.get("successful"):
                self.client_id = msg["clientId"]
                return
        raise ConnectionError(f"Handshake failed: {resp}")

    def _subscribe(self, subscription):
        resp = self._post([{
            "channel": "/meta/subscribe",
            "id": self._next_id(),
            "clientId": self.client_id,
            "subscription": subscription,
        }])
        # Process any state data that comes back with the subscribe response
        self._process_messages(resp)
        for msg in resp:
            if msg.get("channel") == "/meta/subscribe":
                if not msg.get("successful"):
                    raise ConnectionError(f"Subscribe to {subscription} failed: {msg}")

    def _publish(self, channel, data):
        """Publish a message to a service channel. Returns response messages."""
        resp = self._post([{
            "channel": channel,
            "id": self._next_id(),
            "clientId": self.client_id,
            "data": data,
        }])
        self._process_messages(resp)
        return resp

    def _long_poll(self):
        """Single long-poll request. Returns response messages."""
        try:
            resp = self._session.post(
                self.base_url,
                json=[{
                    "channel": "/meta/connect",
                    "connectionType": "callback-polling",
                    "id": self._next_id(),
                    "clientId": self.client_id,
                }],
                headers={"Content-Type": "application/json;charset=UTF8"},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except (requests.Timeout, requests.ConnectionError):
            return []

    def _process_messages(self, messages):
        """Process pushed state updates from the device."""
        with self._lock:
            for msg in messages:
                channel = msg.get("channel", "")
                data = msg.get("data")
                if data is None:
                    continue
                if channel == "/ravenna/query":
                    result = data.get("result", [])
                    if result:
                        self._query_results = result
                        self._query_event.set()
                elif channel in ("/ravenna/settings", "/ravenna/status"):
                    path = data.get("path", "")
                    value = data.get("value")
                    if path == "$" and channel == "/ravenna/status":
                        # Status root has _modules/state — store separately
                        self._state["$._status"] = value
                    elif path == "$" and channel == "/ravenna/settings":
                        # Settings root has identity, actions, sessions, etc.
                        self._state["$"] = value
                    else:
                        self._state[path] = value

    def _poll_loop(self):
        """Background thread: long-poll for state updates."""
        while not self._stop.is_set():
            messages = self._long_poll()
            if messages:
                self._process_messages(messages)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self):
        """Handshake, subscribe, and request initial state dump."""
        self._handshake()

        # Subscribe sequence matches ANEMAN exactly (settings subscribed twice)
        for sub in ("/ravenna/settings", "/ravenna/query",
                     "/ravenna/settings", "/ravenna/status"):
            self._subscribe(sub)

        # Request full state dump (separate from connect poll)
        self._publish("/service/ravenna/commands", {"command": "update"})

        # Poll to receive the initial state dump (device pushes on /meta/connect)
        # The device sends settings across multiple connect responses:
        #   1st: root $ (identity, actions, etc.)
        #   2nd: sources
        #   3rd: sinks + PTP status
        got_identity = False
        extra_polls = 0
        for _ in range(20):
            messages = self._long_poll()
            if messages:
                self._process_messages(messages)
            with self._lock:
                root = self._state.get("$", {})
                if isinstance(root.get("identity"), dict):
                    got_identity = True
            if got_identity:
                extra_polls += 1
                if extra_polls >= 3:
                    break

        # Start background polling
        self._stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        self._connected.set()

    def close(self):
        """Stop polling and close session."""
        self._stop.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=3)
        self._session.close()
        self._connected.clear()

    # ------------------------------------------------------------------
    # Read state
    # ------------------------------------------------------------------

    def _get_state(self, path, default=None):
        with self._lock:
            return self._state.get(path, default)

    def get_full_state(self):
        """Return the complete cached state dictionary."""
        with self._lock:
            return dict(self._state)

    def get_identity(self):
        """Device identity: vendor, product, name, serial, firmware."""
        root = self._get_state("$", {})
        identity = root.get("identity", {})
        return {
            "vendor": identity.get("vendor"),
            "product": identity.get("product"),
            "name": identity.get("name"),
            "serial": identity.get("serial"),
            "firmware": root.get("_firmware_version"),
            "zman": identity.get("zman", {}),
        }

    def get_ptp_status(self):
        """PTP clock status: GMID, lock, jitter."""
        return self._get_state("$.network.PTP.Status", {})

    def get_sources(self):
        """List output source sessions (what the device sends)."""
        root = self._get_state("$", {})
        raw = root.get("sessions", {}).get("sources", [])
        results = []
        for value in raw:
            streams = value.get("streams", [])
            stream_info = streams[0] if streams else {}
            results.append({
                "id": value.get("id"),
                "name": value.get("name"),
                "enabled": value.get("enabled"),
                "address": stream_info.get("address"),
                "codec": stream_info.get("codec"),
                "payload_type": stream_info.get("payload_type"),
                "frame_size": stream_info.get("frameSize"),
                "map": stream_info.get("map", []),
                "state_code": value.get("state", {}).get("code"),
            })
        return sorted(results, key=lambda s: s.get("id", 0))

    def get_sinks(self):
        """List input sink sessions (what the device receives)."""
        root = self._get_state("$", {})
        raw = root.get("sessions", {}).get("sinks", [])
        if not raw:
            # Fall back to individually pushed sink updates
            bulk = self._get_state("$.sessions.sinks")
            if isinstance(bulk, list):
                raw = bulk
        return self._parse_sinks(raw)

    def _parse_sinks(self, sinks):
        results = []
        for s in sinks:
            if not isinstance(s, dict):
                continue
            state = s.get("state", {})
            streams = s.get("streams", [])
            results.append({
                "id": s.get("id"),
                "name": s.get("name"),
                "source": s.get("source"),
                "manual_sdp": s.get("manual_SDP"),
                "delay": s.get("delay"),
                "accept_less_channels": s.get("accept_less_channels"),
                "state_code": state.get("code"),
                "rtp_state": state.get("rtp_state"),
                "sdp_name": state.get("sdp", {}).get("name"),
                "sdp_text": state.get("sdp", {}).get("sdp"),
                "streams": streams,
            })
        return sorted(results, key=lambda s: s.get("id", 0))

    def get_connections(self):
        """List internal routing connections."""
        root = self._get_state("$", {})
        conns = root.get("_connections", [])
        if not conns:
            conns = self._get_state("$._connections", [])
        if not conns:
            # Check individual connection updates
            results = []
            with self._lock:
                for path, value in self._state.items():
                    if "_connections[" in path and isinstance(value, dict):
                        results.append(value)
            return sorted(results, key=lambda c: c.get("id", 0))
        return conns

    def get_ios(self):
        """List IO groups (Stream, OEM I2S, etc.)."""
        root = self._get_state("$", {})
        raw = root.get("ios", [])
        if not raw:
            raw = self._get_state("$.ios", [])
        results = []
        for io in raw:
            results.append({
                "id": io.get("id"),
                "name": io.get("name"),
                "type": io.get("type"),
                "num_ins": len(io.get("ins", [])),
                "num_outs": len(io.get("outs", [])),
            })
        return results

    def get_discovered_sources(self, logger=None):
        """Query SAP-discovered remote sources available on the network.

        Returns a list of SAP source URIs (e.g. 'sap://MyStream').
        These are streams announced by other devices that this module can receive.
        """
        # Clear previous results and send query
        self._query_event.clear()
        self._query_results = []
        resp = self._publish("/service/ravenna/query", {"query": "sessions"})
        # Check if results came in the publish response
        if self._query_results:
            return list(self._query_results)
        # Results may arrive on the background long-poll instead — wait
        self._query_event.wait(timeout=5)
        return list(self._query_results)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def create_path(self, source, output_channels=None, delay=0,
                    accept_less_channels=True, overwrite=True,
                    io_group_id="30", manual_sdp=None):
        """Route an AES67/SAP source to output channels.

        Args:
            source: SAP URI (e.g. "sap://MyStream") or session name.
                    Prefix "sap://" is added if not present.
            output_channels: List of OEM I2S channel IDs (0-15).
                             Defaults to [8, 9] (Lyngdorf L/R).
            delay: Playout delay in samples.
            accept_less_channels: Accept source with fewer channels.
            overwrite: Overwrite existing path on same outputs.
            io_group_id: Target IO group ("30" = OEM I2S on Lyngdorf).
            manual_sdp: Optional raw SDP string. If provided, used instead
                        of SAP discovery.
        """
        if output_channels is None:
            output_channels = [8, 9]

        if not source.startswith("sap://"):
            source = f"sap://{source}"

        outs = [
            [{"io_group_id": str(io_group_id), "channel_id": ch}]
            for ch in output_channels
        ]

        sink = {
            "source": source,
            "name": source,
            "delay": delay,
            "accept_less_channels": accept_less_channels,
        }
        if manual_sdp:
            sink["manual_SDP"] = manual_sdp

        data = {
            "path": "$.actions.create_path",
            "value": {
                "overwrite": overwrite,
                "outs": outs,
                "session_sink": sink,
            },
        }
        return self._publish("/service/ravenna/settings", data)

    def delete_path(self, sink_id, dont_delete_source=False):
        """Disconnect a sink by ID.

        Args:
            sink_id: The sink session ID to disconnect.
            dont_delete_source: Keep the source entry after disconnecting.
        """
        data = {
            "path": "$.actions.delete_path",
            "value": {
                "dont_delete_source": dont_delete_source,
                "session_sink": {"id": sink_id},
            },
        }
        return self._publish("/service/ravenna/settings", data)

    def delete_all_sinks(self):
        """Delete all sink sessions."""
        return self._publish("/service/ravenna/settings", {
            "path": "$.actions.delete_all_sinks",
            "value": {},
        })

    def refresh(self):
        """Request a fresh state dump from the device."""
        self._publish("/service/ravenna/commands", {"command": "update"})
        # Give device time to push updates
        time.sleep(0.5)
        messages = self._long_poll()
        if messages:
            self._process_messages(messages)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()


# ----------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------

def _test(host="10.11.7.88"):
    """Quick self-test: connect, dump state, disconnect."""
    print(f"Connecting to ZMAN at {host}...")
    with ZMANClient(host) as client:
        identity = client.get_identity()
        print(f"\nIdentity:")
        for k, v in identity.items():
            print(f"  {k}: {v}")

        ptp = client.get_ptp_status()
        print(f"\nPTP Status:")
        for k, v in ptp.items():
            print(f"  {k}: {v}")

        ios = client.get_ios()
        print(f"\nIO Groups:")
        for io in ios:
            print(f"  {io['id']}: {io['name']} ({io['num_ins']} in / {io['num_outs']} out)")

        sources = client.get_sources()
        print(f"\nSources ({len(sources)}):")
        for s in sources:
            print(f"  [{s['id']}] {s['name']} → {s['address']} "
                  f"({s['codec']}, PT={s['payload_type']}, map={s['map']})")

        sinks = client.get_sinks()
        print(f"\nSinks ({len(sinks)}):")
        for s in sinks:
            state = {0: "idle", 1: "connecting", 2: "connected", 3: "connected+stats"}
            rtp = {1: "active", 2: "idle", -1: "error/muted"}
            print(f"  [{s['id']}] {s['name']}")
            print(f"    source: {s['source']}")
            print(f"    state: {state.get(s['state_code'], s['state_code'])} "
                  f"rtp: {rtp.get(s['rtp_state'], s['rtp_state'])}")
            if s['sdp_name']:
                print(f"    sdp: {s['sdp_name']}")

        conns = client.get_connections()
        print(f"\nConnections ({len(conns)}):")
        for c in conns:
            inp = c.get("in", {})
            out = c.get("out", {})
            print(f"  [{c['id']}] in(grp={inp.get('io_group_id')}, "
                  f"ch={inp.get('channel_id')}) → "
                  f"out(grp={out.get('io_group_id')}, "
                  f"ch={out.get('channel_id')})")

    print("\nDone.")


if __name__ == "__main__":
    import sys
    host = sys.argv[1] if len(sys.argv) > 1 else "10.11.7.88"
    _test(host)
