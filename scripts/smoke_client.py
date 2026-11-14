"""Read-only smoke test of the eSmart client against the real controller.

Usage: .venv/bin/python scripts/smoke_client.py [seconds]
Uses the paired test device credentials from spike/.esmart-test.json.
Sends only GET requests. allowed_nodes is empty, so commands are impossible.
"""
import asyncio, json, logging, sys, time, pathlib, importlib.util

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("esmart_client", ROOT / "custom_components/esmart/client.py")
client_mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(client_mod)

CREDS = ROOT / "spike" / ".esmart-test.json"

async def main(seconds: int) -> None:
    creds = json.loads(CREDS.read_text())
    c = client_mod.ESmartClient(creds["jid"], creds["password"], allowed_nodes=())
    events = []
    c.add_listener(lambda changed: events.append(changed))
    t0 = time.monotonic()
    await c.start()
    try:
        await c.wait_ready(30)
        print(f"ready after {time.monotonic()-t0:.1f}s, apartment {c.apartment_id}, room {c.room}")
        print(f"nodes: {len(c.nodes)}, meters: {len(c.meters)}, scenes: {len(c.scenes)}, extra: {list(c.extra)}")
        print("structure nodes:", sum(1 for _ in client_mod.iter_nodes(c.structure)))
        try:
            await c.command(41, onoff="on")
        except client_mod.ESmartNotAllowed as e:
            print("guard ok (nothing sent):", e)
        await asyncio.sleep(seconds)
        print(f"available={c.available}, listener events={len(events)}")
    finally:
        await c.stop()

logging.basicConfig(level=logging.INFO)
logging.getLogger("slixmpp").setLevel(logging.WARNING)
asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 10))
