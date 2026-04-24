import asyncio
import time
import httpx
import json

BASE_URL = "http://localhost:8000/api"

async def measure_time(name, aw):
    start = time.time()
    res = await aw
    duration = time.time() - start
    print(f"[{name}] took {duration:.4f}s - Status: {res.status_code}")
    if res.status_code >= 400:
        print(f"Error: {res.text}")
    return res.json() if res.status_code == 200 else {}, duration

async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Health
        health_data, _ = await measure_time("Health", client.get(f"{BASE_URL}/health"))
        print("  -> Provider:", health_data.get("provider"))
        print("  -> Available tiers:", health_data.get("available_tiers", []))

        # 2. Strategies
        strats_data, _ = await measure_time("Strategies", client.get(f"{BASE_URL}/strategies"))
        if isinstance(strats_data, list):
            strats = strats_data
        else:
            strats = strats_data.get("items", [])
        print(f"  -> Found {len(strats)} strategies")
        if len(strats) > 0:
            print(f"  -> Example: {strats[0].get('name')} - {strats[0].get('tagline')}")
        
        # 3. History
        hist_data, _ = await measure_time("History", client.get(f"{BASE_URL}/history?limit=5"))
        items = hist_data.get("items", [])
        print(f"  -> History items: {len(items)}, Total: {hist_data.get('total')}")

        # 4. Match
        match_payload = {"prompt_text": "Write a fast api python endpoint for upload"}
        match_data, _ = await measure_time("Match", client.post(f"{BASE_URL}/clusters/match", json=match_payload))
        print(f"  -> Matched patterns length: {len(match_data.get('suggested_patterns', []) or match_data.get('patterns', []))} ")

        # 5. Delete flow — create an opt, delete via bulk endpoint, verify gone
        create_resp = await client.post(
            f"{BASE_URL}/optimize",
            json={"raw_prompt": "temp smoke-test prompt for delete"},
        )
        if create_resp.status_code != 200:
            print(f"[DeleteSmoke] create failed: {create_resp.status_code}; skipping")
        else:
            opt = create_resp.json()
            opt_id = opt.get("id") or opt.get("trace_id")
            if not opt_id:
                print(f"[DeleteSmoke] could not extract id from create response; skipping")
            else:
                delete_data, _ = await measure_time(
                    "DeleteBulk",
                    client.post(
                        f"{BASE_URL}/optimizations/delete",
                        json={"ids": [opt_id]},
                    ),
                )
                print(f"  -> deleted={delete_data.get('deleted')}, requested={delete_data.get('requested')}")
                print(f"  -> affected_cluster_ids={delete_data.get('affected_cluster_ids')}")
                print(f"  -> affected_project_ids={delete_data.get('affected_project_ids')}")

                # Verify the row is gone from history
                hist_after, _ = await measure_time(
                    "HistoryAfterDelete",
                    client.get(f"{BASE_URL}/history?limit=50"),
                )
                remaining_ids = {i.get("id") for i in hist_after.get("items", [])}
                if opt_id in remaining_ids:
                    print(f"  -> FAIL: {opt_id} still in history after delete")
                else:
                    print(f"  -> OK: {opt_id} removed from history")

        print("\nAll workflow logic tests completed.")

if __name__ == "__main__":
    asyncio.run(main())
