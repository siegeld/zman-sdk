#!/usr/bin/env python3
"""ZMAN CLI — Interactive command-line tool for ZMAN/RAVENNA modules.

Usage:
    python3 cli.py [host]          # Interactive mode
    python3 cli.py [host] status   # One-shot commands
    python3 cli.py [host] sources
    python3 cli.py [host] sinks
    python3 cli.py [host] connections
    python3 cli.py [host] ptp
    python3 cli.py [host] ios
    python3 cli.py [host] browse
    python3 cli.py [host] route <sap-name> <ch1,ch2,...>
    python3 cli.py [host] route-sdp <sdp-file> <ch1,ch2,...>
    python3 cli.py [host] disconnect <sink-id>
    python3 cli.py [host] disconnect-all
"""

import cmd
import json
import readline
import sys
import textwrap

from zman import ZMANClient, __version__

DEFAULT_HOST = "10.11.7.88"


class ZMANShell(cmd.Cmd):
    """Interactive ZMAN control shell."""

    intro = None
    prompt = "zman> "

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._discovered = []
        identity = client.get_identity()
        name = identity.get("name", "unknown")
        product = identity.get("product", "")
        fw = identity.get("firmware", "")
        self.intro = (
            f"ZMAN CLI v{__version__}\n"
            f"Connected to {name} ({product}, fw {fw})\n"
            f"Type 'help' for commands, 'quit' to exit.\n"
        )
        self.prompt = f"{name}> "

    # -- Display helpers --

    def _print_table(self, headers, rows):
        """Print a simple aligned table."""
        if not rows:
            print("  (none)")
            return
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(str(cell)))
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        print("  " + fmt.format(*headers))
        print("  " + "  ".join("-" * w for w in widths))
        for row in rows:
            print("  " + fmt.format(*[str(c) for c in row]))

    # -- Commands --

    def do_status(self, arg):
        """Show device identity and PTP status."""
        identity = self.client.get_identity()
        print(f"\n  Device:   {identity.get('vendor')} {identity.get('product')}")
        print(f"  Name:     {identity.get('name')}")
        print(f"  Serial:   {identity.get('serial')}")
        print(f"  Firmware: {identity.get('firmware')}")
        zman = identity.get("zman", {})
        if zman:
            print(f"  ZMAN:     {zman.get('product')} ({zman.get('name')})")

        ptp = self.client.get_ptp_status()
        lock = {0: "unlocked", 1: "locking", 2: "locked"}.get(
            ptp.get("LockStatus"), str(ptp.get("LockStatus"))
        )
        print(f"\n  PTP GMID:   {ptp.get('GMID')}")
        print(f"  PTP Lock:   {lock}")
        print(f"  Net Jitter: {ptp.get('NetworkJitter')}")
        print(f"  Clk Jitter: {ptp.get('ClockJitter')}")
        print()

    def do_browse(self, arg):
        """List SAP-discovered remote sources available on the network.

        These are streams from other devices that can be routed to this device.
        Use 'route <number> <channels>' to route by browse number.
        """
        self._discovered = self.client.get_discovered_sources()
        print(f"\n  Discovered {len(self._discovered)} remote source(s):\n")
        for i, s in enumerate(self._discovered, 1):
            print(f"  {i:3d}. {s}")
        print()

    def do_sources(self, arg):
        """List output source streams (what the device sends to the network)."""
        sources = self.client.get_sources()
        rows = []
        for s in sources:
            ch_map = ",".join(str(c) for c in s.get("map", []))
            rows.append([
                s["id"],
                s["name"],
                s.get("address", ""),
                s.get("codec", ""),
                f"PT={s.get('payload_type', '')}",
                f"[{ch_map}]",
            ])
        print()
        self._print_table(["ID", "Name", "Address", "Codec", "PT", "Map"], rows)
        print()

    def do_sinks(self, arg):
        """List input sink sessions (what the device receives from the network)."""
        sinks = self.client.get_sinks()
        state_map = {0: "idle", 1: "connecting", 2: "connected", 3: "connected+stats"}
        rtp_map = {1: "active", 2: "idle", -1: "error/muted"}
        rows = []
        for s in sinks:
            rows.append([
                s["id"],
                s.get("source", "")[:40],
                state_map.get(s.get("state_code"), str(s.get("state_code"))),
                rtp_map.get(s.get("rtp_state"), str(s.get("rtp_state"))),
                s.get("sdp_name", "") or "",
            ])
        print()
        self._print_table(["ID", "Source", "State", "RTP", "SDP Name"], rows)
        print()

    def do_connections(self, arg):
        """List internal routing connections between IO groups."""
        conns = self.client.get_connections()
        rows = []
        for c in conns:
            inp = c.get("in", {})
            out = c.get("out", {})
            rows.append([
                c["id"],
                f"grp={inp.get('io_group_id')}",
                f"ch={inp.get('channel_id')}",
                "→",
                f"grp={out.get('io_group_id')}",
                f"ch={out.get('channel_id')}",
            ])
        print()
        self._print_table(["ID", "In Group", "In Ch", "", "Out Group", "Out Ch"], rows)
        print()

    def do_ios(self, arg):
        """List IO groups (Stream, OEM I2S, etc.)."""
        ios = self.client.get_ios()
        rows = []
        for io in ios:
            rows.append([
                io["id"],
                io["name"],
                io.get("type", ""),
                io["num_ins"],
                io["num_outs"],
            ])
        print()
        self._print_table(["ID", "Name", "Type", "Inputs", "Outputs"], rows)
        print()

    def do_ptp(self, arg):
        """Show PTP clock status."""
        ptp = self.client.get_ptp_status()
        lock = {0: "unlocked", 1: "locking", 2: "locked"}.get(
            ptp.get("LockStatus"), str(ptp.get("LockStatus"))
        )
        print(f"\n  GMID:       {ptp.get('GMID')}")
        print(f"  Lock:       {lock}")
        print(f"  Net Jitter: {ptp.get('NetworkJitter')}")
        print(f"  Clk Jitter: {ptp.get('ClockJitter')}")
        ifaces = ptp.get("Interfaces", [])
        for i, iface in enumerate(ifaces):
            state_map = {8: "master", 4: "slave", 2: "listening"}
            st = state_map.get(iface.get("State"), str(iface.get("State")))
            print(f"  Interface {i}: {st} (GMID: {iface.get('GMID')})")
        print()

    def do_route(self, arg):
        """Route a SAP stream to output channels.

        Usage: route <sap-name-or-number> [channel1,channel2,...]

        Channels default to 8,9 (Lyngdorf L/R) if omitted.

        Example: route 35              (uses default channels 8,9)
                 route 35 8,9
                 route AES67-Lyngdorf_2 8,9
                 route "sap://danterbr3-villa-sonos : 2" 8,9
        """
        parts = arg.strip().rsplit(None, 1)
        if not parts:
            print("Usage: route <sap-name-or-number> [ch1,ch2,...]")
            print("Example: route 35")
            return
        # Check if last part looks like channels (digits and commas)
        if len(parts) == 2 and all(c.isdigit() or c in ",  " for c in parts[1]):
            source = parts[0].strip('"').strip("'")
            chan_str = parts[1]
        else:
            source = arg.strip().strip('"').strip("'")
            chan_str = "8,9"
        # Resolve browse number to source name
        try:
            idx = int(source)
            discovered = getattr(self, "_discovered", None)
            if not discovered:
                print("  Fetching discovered sources...")
                self._discovered = self.client.get_discovered_sources()
                discovered = self._discovered
            if 1 <= idx <= len(discovered):
                source = discovered[idx - 1]
                print(f"  Resolved #{idx} → {source}")
            else:
                print(f"  Invalid browse number {idx} (run 'browse' to see list)")
                return
        except ValueError:
            pass
        try:
            channels = [int(c.strip()) for c in chan_str.split(",")]
        except ValueError:
            print("Channels must be comma-separated integers (e.g. 8,9)")
            return
        print(f"  Routing '{source}' → OEM channels {channels}...")
        resp = self.client.create_path(source=source, output_channels=channels)
        ok = any(m.get("successful") for m in resp)
        print(f"  {'OK' if ok else 'Failed'}")
        print()

    def do_route_sdp(self, arg):
        """Route using a manual SDP file (bypasses SAP discovery).

        Usage: route-sdp <sdp-file-path> <channel1,channel2,...>

        Example: route-sdp /path/to/stream.sdp 8,9
        """
        parts = arg.strip().rsplit(None, 1)
        if len(parts) != 2:
            print("Usage: route-sdp <sdp-file> <ch1,ch2,...>")
            return
        sdp_path = parts[0].strip('"').strip("'")
        try:
            channels = [int(c.strip()) for c in parts[1].split(",")]
        except ValueError:
            print("Channels must be comma-separated integers")
            return
        try:
            with open(sdp_path) as f:
                sdp_text = f.read()
        except FileNotFoundError:
            print(f"  File not found: {sdp_path}")
            return
        # Extract session name from SDP
        name = "manual-sdp"
        for line in sdp_text.splitlines():
            if line.startswith("s="):
                name = line[2:].strip()
                break
        print(f"  Routing SDP '{name}' → OEM channels {channels}...")
        resp = self.client.create_path(
            source=name, output_channels=channels, manual_sdp=sdp_text
        )
        ok = any(m.get("successful") for m in resp)
        print(f"  {'OK' if ok else 'Failed'}")
        print()

    def do_disconnect(self, arg):
        """Disconnect a sink by ID.

        Usage: disconnect <sink-id>
        """
        try:
            sink_id = int(arg.strip())
        except ValueError:
            print("Usage: disconnect <sink-id>")
            print("Use 'sinks' to see active sink IDs.")
            return
        print(f"  Disconnecting sink {sink_id}...")
        resp = self.client.delete_path(sink_id)
        ok = any(m.get("successful") for m in resp)
        print(f"  {'OK' if ok else 'Failed'}")
        print()

    def do_disconnect_all(self, arg):
        """Disconnect all sinks."""
        print("  Deleting all sinks...")
        resp = self.client.delete_all_sinks()
        ok = any(m.get("successful") for m in resp)
        print(f"  {'OK' if ok else 'Failed'}")
        print()

    def do_refresh(self, arg):
        """Re-fetch device state."""
        print("  Refreshing...")
        self.client.refresh()
        print("  Done.")
        print()

    def do_dump(self, arg):
        """Dump full cached state as JSON (for debugging)."""
        state = self.client.get_full_state()
        print(json.dumps(state, indent=2, default=str))

    def do_quit(self, arg):
        """Exit the shell."""
        print("Bye.")
        return True

    do_exit = do_quit
    do_EOF = do_quit

    def emptyline(self):
        pass

    def default(self, line):
        print(f"Unknown command: {line}")
        print("Type 'help' for available commands.")


def run_oneshot(client, command, args):
    """Run a single command and exit."""
    commands = {
        "status": lambda: ZMANShell(client).do_status(""),
        "sources": lambda: ZMANShell(client).do_sources(""),
        "sinks": lambda: ZMANShell(client).do_sinks(""),
        "connections": lambda: ZMANShell(client).do_connections(""),
        "ptp": lambda: ZMANShell(client).do_ptp(""),
        "ios": lambda: ZMANShell(client).do_ios(""),
        "browse": lambda: ZMANShell(client).do_browse(""),
    }
    if command in commands:
        commands[command]()
    elif command == "route" and len(args) >= 1:
        ZMANShell(client).do_route(" ".join(args))
    elif command == "route-sdp" and len(args) >= 2:
        ZMANShell(client).do_route_sdp(f"{args[0]} {args[1]}")
    elif command == "disconnect" and len(args) >= 1:
        ZMANShell(client).do_disconnect(args[0])
    elif command == "disconnect-all":
        ZMANShell(client).do_disconnect_all("")
    elif command == "dump":
        ZMANShell(client).do_dump("")
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


def main():
    args = sys.argv[1:]

    if args and args[0] in ("--version", "-V"):
        print(f"zman-cli {__version__}")
        return

    # Parse host
    host = DEFAULT_HOST
    if args and not args[0].startswith("-") and "." in args[0]:
        host = args.pop(0)

    print(f"Connecting to {host}...")
    with ZMANClient(host) as client:
        if args:
            # One-shot mode
            command = args.pop(0)
            run_oneshot(client, command, args)
        else:
            # Interactive mode
            shell = ZMANShell(client)
            try:
                shell.cmdloop()
            except KeyboardInterrupt:
                print("\nBye.")


if __name__ == "__main__":
    main()
