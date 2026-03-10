# ZMAN SDK

Python client for Merging Technologies ZMAN/RAVENNA OEM modules (e.g. Lyngdorf MP-60).

Controls the ZMAN module via its CometD/Bayeux HTTP API on port 80. Protocol was reverse-engineered from ANEMAN network captures.

## Install

```bash
pip install requests
```

Single file — just copy `zman.py` into your project or add this repo as a dependency.

## Quick Start

```python
from zman import ZMANClient

with ZMANClient("10.11.7.88") as client:
    print(client.get_identity())
    print(client.get_sources())
    print(client.get_sinks())
    print(client.get_connections())
    print(client.get_discovered_sources())  # SAP sources on the network
```

## Self-Test

```bash
python3 zman.py 10.11.7.88
```

Example output:

```
Identity:
  vendor: Steinway Lyngdorf
  product: MP-60
  name: MP60_111185
  serial: 111185
  firmware: 1.6.1b57204
  zman: {'vendor': 'Merging Technologies', 'product': 'ZMAN Family', 'serial': '111185', 'name': 'ZMAN_Z010'}

PTP Status:
  GMID: 00-1D-C1-FF-FE-83-C3-5D
  LockStatus: 2
  NetworkJitter: -16549
  ClockJitter: -351

IO Groups:
  1: Stream (64 in / 64 out)
  30: OEM I2S I/O (16 in / 16 out)

Sources (2):
  [1] MP60_111185_1 → 239.1.7.88 (L24, PT=98, map=[0, 1, 2, 3, 4, 5, 6, 7])
  [2] MP60_111185_2 → 239.2.7.88 (L24, PT=98, map=[8, 9, 10, 11, 12, 13, 14, 15])

Sinks (1):
  [78] sap://danterbr3-villa-sonos : 2
    source: sap://danterbr3-villa-sonos : 2
    state: connected+stats rtp: active

Connections (18):
  [1] in(grp=30, ch=0) → out(grp=1, ch=0)
  ...
  [19] in(grp=1, ch=0) → out(grp=30, ch=8)
  [20] in(grp=1, ch=1) → out(grp=30, ch=9)
```

## Routing

### Discover SAP Sources

```python
with ZMANClient("10.11.7.88") as client:
    sources = client.get_discovered_sources()
    for s in sources:
        print(s)  # e.g. "sap://danterbr3-villa-sonos : 2"
```

### Route a SAP Stream

The ZMAN module discovers SAP announcements on the network. Reference them by session name.
Output channels default to `[8, 9]` (Lyngdorf L/R).

```python
with ZMANClient("10.11.7.88") as client:
    # Route SAP stream to default OEM I2S channels 8,9 (L/R on Lyngdorf)
    client.create_path(source="sap://AES67-Lyngdorf_2")

    # Or specify channels explicitly
    client.create_path(
        source="sap://AES67-Lyngdorf_2",
        output_channels=[8, 9],
    )
```

### Route with Manual SDP (no SAP needed)

Bypass SAP discovery by providing the SDP directly:

```python
sdp = """v=0
o=- 1 0 IN IP4 10.11.7.81
s=MyStream
c=IN IP4 239.69.83.2/15
t=0 0
m=audio 5004 RTP/AVP 98
a=rtpmap:98 L24/48000/2
a=ptime:1
a=ts-refclk:ptp=IEEE1588-2008:00-1D-C1-FF-FE-83-C3-5D:0
a=mediaclk:direct=0
"""

with ZMANClient("10.11.7.88") as client:
    client.create_path(
        source="manual://MyStream",
        output_channels=[8, 9],
        manual_sdp=sdp,
    )
```

### Disconnect a Sink

```python
with ZMANClient("10.11.7.88") as client:
    sinks = client.get_sinks()
    for s in sinks:
        client.delete_path(s["id"])
```

### Disconnect All Sinks

```python
with ZMANClient("10.11.7.88") as client:
    client.delete_all_sinks()
```

## API Reference

### Connection

| Method | Description |
|--------|-------------|
| `ZMANClient(host, port=80)` | Create client. Does not connect yet. |
| `connect()` | CometD handshake, subscribe to channels, fetch initial state. |
| `close()` | Stop background polling, close HTTP session. |
| `refresh()` | Re-request state dump from device. |

Supports context manager: `with ZMANClient(host) as client: ...`

### Device Info

| Method | Returns |
|--------|---------|
| `get_identity()` | `{vendor, product, name, serial, firmware, zman}` |
| `get_ptp_status()` | `{GMID, LockStatus, NetworkJitter, ClockJitter, Interfaces}` |
| `get_ios()` | `[{id, name, type, num_ins, num_outs}]` |
| `get_full_state()` | Complete cached state dictionary (for debugging) |

### Sessions

| Method | Returns |
|--------|---------|
| `get_sources()` | `[{id, name, enabled, address, codec, payload_type, frame_size, map, state_code}]` |
| `get_sinks()` | `[{id, name, source, manual_sdp, delay, accept_less_channels, state_code, rtp_state, sdp_name, sdp_text, streams}]` |
| `get_discovered_sources()` | `["sap://...", ...]` — SAP sources visible on the network |
| `get_connections()` | `[{id, in: {io_group_id, channel_id}, out: {io_group_id, channel_id}, delay, state}]` |

### Routing Actions

#### `create_path(source, output_channels, **kwargs)`

Route an AES67 stream to device output channels.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `source` | str | required | SAP URI or session name. `sap://` prefix added if missing. |
| `output_channels` | list[int] | [8, 9] | OEM I2S channel IDs (0-15). Defaults to Lyngdorf L/R. |
| `delay` | int | 0 | Playout delay in samples. |
| `accept_less_channels` | bool | True | Accept source with fewer channels than outputs. |
| `overwrite` | bool | True | Overwrite existing path on same outputs. |
| `io_group_id` | str | "30" | Target IO group. "30" = OEM I2S on Lyngdorf. |
| `manual_sdp` | str | None | Raw SDP string. Bypasses SAP discovery if provided. |

#### `delete_path(sink_id, dont_delete_source=False)`

Disconnect a sink by its ID.

#### `delete_all_sinks()`

Remove all sink sessions.

## IO Groups

The ZMAN module has two IO groups:

| ID | Name | Channels | Description |
|----|------|----------|-------------|
| `"1"` | Stream | 64 in / 64 out | RAVENNA network audio channels |
| `"30"` | OEM I2S I/O | 16 in / 16 out | Lyngdorf's internal audio bus |

Connections route between these groups:
- **OEM → Stream** (channels 0-15 → 0-15): Lyngdorf sends audio to the network
- **Stream → OEM**: Network audio routed to Lyngdorf inputs (created by `create_path`)

## Sink State Codes

| Code | Meaning |
|------|---------|
| 0 | Idle / disconnected |
| 1 | Connecting |
| 2 | Connected / receiving |
| 3 | Connected with stats |

## RTP State

| Code | Meaning |
|------|---------|
| 1 | Active / receiving packets |
| 2 | Idle |
| -1 | Error / muted |

## Protocol Details

The ZMAN module runs an HTTP server on port 80 with a CometD/Bayeux endpoint at `/cometd`.

### Connection Sequence

1. **Handshake** — `POST /cometd` with `/meta/handshake` → returns `clientId`
2. **Subscribe** — Subscribe to `/ravenna/settings` (twice), `/ravenna/query`, `/ravenna/status`
3. **Update command** — Publish `{"command": "update"}` to `/service/ravenna/commands`
4. **Long-poll** — `POST /cometd` with `/meta/connect` — device pushes state updates as responses

### CometD Channels

| Channel | Direction | Purpose |
|---------|-----------|---------|
| `/ravenna/settings` | device → client | Full device config (identity, sessions, ios, connections) |
| `/ravenna/status` | device → client | PTP status, CPU/memory, sink RTP state |
| `/ravenna/query` | device → client | Query responses |
| `/service/ravenna/commands` | client → device | Commands (e.g. `{"command": "update"}`) |
| `/service/ravenna/settings` | client → device | Actions (create_path, delete_path, etc.) |

### Action Payloads

Actions are sent as JSON to `/service/ravenna/settings` with a JSONPath-style `path`:

```json
{
  "channel": "/service/ravenna/settings",
  "data": {
    "path": "$.actions.create_path",
    "value": {
      "overwrite": true,
      "outs": [
        [{"io_group_id": "30", "channel_id": 8}],
        [{"io_group_id": "30", "channel_id": 9}]
      ],
      "session_sink": {
        "source": "sap://AES67-Stream",
        "name": "sap://AES67-Stream",
        "delay": 0,
        "accept_less_channels": true
      }
    }
  }
}
```

### Available Actions

| Path | Description |
|------|-------------|
| `$.actions.create_path` | Route a stream to output channels |
| `$.actions.delete_path` | Disconnect a sink |
| `$.actions.mute` | Mute |
| `$.actions.create_connections` | Create internal IO connections |
| `$.actions.delete_connections` | Delete internal IO connections |
| `$.actions.delete_all_connections` | Delete all connections |
| `$.actions.delete_all_sources` | Remove all sources |
| `$.actions.delete_all_sinks` | Remove all sinks |
| `$.actions.load_from_preset` | Load preset |
| `$.actions.save_to_preset` | Save preset |
| `$.actions.delete_preset` | Delete preset |
| `$.actions.clear_all_peers` | Clear all peers |
| `$.actions.usb_reset` | USB reset |
