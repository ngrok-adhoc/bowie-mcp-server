import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastmcp import Client

ROOT = Path(__file__).resolve().parents[1]
TOOLS = {
    *(
        f"{action}_dog_{kind}"
        for action in ("add", "review")
        for kind in ("park", "cafe")
    ),
    "find_dog_parks",
    "find_dog_cafes",
}


@contextmanager
def running_server(project: Path) -> Iterator[str]:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        port = candidate.getsockname()[1]
    env = {**os.environ, "BIND_HOST": "127.0.0.1"}
    env.pop("STORAGE", None)
    process = subprocess.Popen(
        [sys.executable, "-c", f"from mcp_server import main; main({port})"],
        cwd=project,
        env=env,
        stdout=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


async def call(
    client: Client[Any], tool: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await client.call_tool(tool, arguments)
    assert response.structured_content is not None
    return response.structured_content


class BowieHTTPTest(unittest.TestCase):
    def test_tools_and_persistence(self) -> None:
        data_file = ROOT / "data" / "places.json"
        original_data = data_file.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            path = project / "data" / "places.json"
            path.parent.mkdir()
            fixture = json.loads(original_data)
            legacy = next(
                place for place in fixture["places"] if place["type"] == "park"
            )
            legacy.pop("ratings", None)
            legacy["rating"] = {"stars": 3, "comment": "Legacy review"}
            legacy["public_ratings"] = [
                {
                    "stars": 4.5,
                    "review_count": 2,
                    "platform": "Google Maps",
                    "scope": "dog_enclosure",
                }
            ]
            legacy["research_notes"] = [
                {
                    "text": "Visitors mention shade.",
                    "kind": "review_summary",
                    "source": "https://example.com/park",
                }
            ]
            legacy.pop("description", None)
            legacy["notes"] = "Legacy shade and water details."
            path.write_text(json.dumps(fixture))
            (project / "mcp_server.py").write_bytes(
                (ROOT / "mcp_server.py").read_bytes()
            )
            with running_server(project) as url:
                ids = asyncio.run(self.exercise_tools(url, legacy["id"]))
            with running_server(project) as url:
                asyncio.run(self.check_restart(url, *ids))
            original = json.loads(original_data)
            stored = json.loads(path.read_bytes())
            self.assertEqual(stored["park_attribution"], original["park_attribution"])
            stored_by_id = {place["id"]: place for place in stored["places"]}
            self.assertEqual(stored_by_id[legacy["id"]]["ratings"], [legacy["rating"]])
            self.assertEqual(
                stored_by_id[legacy["id"]]["public_ratings"], legacy["public_ratings"]
            )
            self.assertEqual(
                stored_by_id[legacy["id"]]["research_notes"], legacy["research_notes"]
            )
            self.assertEqual(stored_by_id[legacy["id"]]["description"], legacy["notes"])
            self.assertNotIn("notes", stored_by_id[legacy["id"]])
            self.assertTrue(
                all(
                    "average_rating" not in place
                    and "comments" not in place
                    and "distance_from_holland_park_km" not in place
                    for place in stored["places"]
                )
            )
            for place in original["places"]:
                if place["type"] == "cafe":
                    self.assertEqual(
                        stored_by_id[place["id"]]["sources"], place["sources"]
                    )
        self.assertEqual(data_file.read_bytes(), original_data)

    async def exercise_tools(self, url: str, legacy_id: str) -> tuple[str, str]:
        async with Client(url) as client:
            self.assertEqual(client.protocol_version, "2026-07-28")
            self.assertIn("with Bowie.", client.instructions or "")
            tools = {tool.name: tool for tool in await client.list_tools()}
            self.assertEqual(set(tools), TOOLS)
            for tool in tools.values():
                self.assertEqual(tool.input_schema.get("type"), "object")
                self.assertIsNotNone(tool.output_schema)
            parks = await call(client, "find_dog_parks", {"country": "aUsTrAlIa"})
            self.assertGreaterEqual(len(parks["places"]), 10)
            legacy = next(
                place for place in parks["places"] if place["id"] == legacy_id
            )
            self.assertEqual(legacy["average_rating"], 3)
            self.assertEqual(
                legacy["comments"], ["Legacy review", "Visitors mention shade."]
            )
            self.assertEqual(legacy["description"], "Legacy shade and water details.")
            cafes = await call(client, "find_dog_cafes", {"puppuccino": True})
            self.assertTrue(cafes["places"])
            self.assertTrue(
                all(
                    c["address"] and c["phone"] and c["description"]
                    for c in cafes["places"]
                )
            )
            # Unknown amenities must not match an explicit false filter.
            parks = await call(
                client,
                "find_dog_parks",
                {"suburb": "HOLLAND PARK", "drinking_water": False},
            )
            self.assertEqual(parks["places"], [])
            place = {"suburb": "Bowie Test", "country": "Australia"}
            park, cafe = await asyncio.gather(
                call(
                    client,
                    "add_dog_park",
                    {"place": {**place, "name": "Demo Park", "drinking_water": False}},
                ),
                call(
                    client,
                    "add_dog_cafe",
                    {
                        "place": {
                            **place,
                            "name": "Demo Café",
                            "description": "The deck is shaded.",
                        }
                    },
                ),
            )
            self.assertEqual(cafe["description"], "The deck is shaded.")
            for tool, radius, expected_id, expected_km, unlocated in (
                ("find_dog_parks", 1, "whites-hill-reserve", 0.55, park),
                ("find_dog_cafes", 1.2, "stomp-espresso", 0.38, cafe),
            ):
                nearby = (await call(client, tool, {"radius_km": radius}))["places"]
                distances = [p["distance_from_holland_park_km"] for p in nearby]
                self.assertEqual(distances, sorted(distances))
                self.assertTrue(all(0 <= distance <= radius for distance in distances))
                self.assertNotIn(unlocated["id"], [p["id"] for p in nearby])
                expected = next(p for p in nearby if p["id"] == expected_id)
                self.assertAlmostEqual(
                    expected["distance_from_holland_park_km"], expected_km, places=2
                )
            parks = await call(
                client, "find_dog_parks", {"radius_km": 1, "suburb": "Camp Hill"}
            )
            self.assertTrue(
                any(p["id"] == "whites-hill-reserve" for p in parks["places"])
            )
            self.assertTrue(all(p["suburb"] == "Camp Hill" for p in parks["places"]))
            invalid_radius = await client.call_tool(
                "find_dog_parks", {"radius_km": 0}, raise_on_error=False
            )
            self.assertTrue(invalid_radius.is_error)
            for invalid in (0, 6, True, 4.5):
                result = await client.call_tool(
                    "review_dog_park",
                    {"place_id": park["id"], "stars": invalid},
                    raise_on_error=False,
                )
                self.assertTrue(result.is_error)
            result = await client.call_tool(
                "review_dog_park",
                {"place_id": cafe["id"], "stars": 3},
                raise_on_error=False,
            )
            self.assertTrue(result.is_error)
            for kind, added in (("park", park), ("cafe", cafe)):
                self.assertIsNone(added.get("average_rating"))
                unrated = await client.call_tool(
                    f"review_dog_{kind}",
                    {"place_id": added["id"], "comment": "Room for Bowie"},
                    raise_on_error=False,
                )
                self.assertTrue(unrated.is_error)
            await call(
                client,
                "review_dog_park",
                {"place_id": park["id"], "stars": 4, "comment": "Room for Bowie"},
            )
            rated_cafe = await call(
                client, "review_dog_cafe", {"place_id": cafe["id"], "stars": 4}
            )
            self.assertEqual(rated_cafe["average_rating"], 4)
            self.assertEqual(rated_cafe["comments"], [])
            updated_park = await call(
                client,
                "review_dog_park",
                {
                    "place_id": park["id"],
                    "stars": 2,
                    "comment": "Water tap near the gate",
                    "source": "https://example.com/park",
                },
            )
            self.assertEqual(
                updated_park["comments"], ["Room for Bowie", "Water tap near the gate"]
            )
            self.assertEqual(updated_park["average_rating"], 3)
            self.assertEqual(
                updated_park["ratings"][-1]["source"], "https://example.com/park"
            )
            rated = await call(
                client,
                "review_dog_cafe",
                {"place_id": cafe["id"], "stars": 5, "comment": "Bowie approved"},
            )
            self.assertEqual(rated["average_rating"], 4.5)
            self.assertEqual(rated["comments"], ["Bowie approved"])
            return park["id"], cafe["id"]

    async def check_restart(self, url: str, park_id: str, cafe_id: str) -> None:
        async with Client(url, mode="legacy") as client:
            self.assertEqual(client.protocol_version, "2025-11-25")
            self.assertEqual({tool.name for tool in await client.list_tools()}, TOOLS)
            parks = await call(
                client,
                "find_dog_parks",
                {"suburb": "BOWIE TEST", "drinking_water": False},
            )
            cafes = await call(client, "find_dog_cafes", {"suburb": "BOWIE TEST"})
            self.assertEqual(
                [(p["id"], p["average_rating"]) for p in parks["places"]],
                [(park_id, 3)],
            )
            self.assertEqual(
                parks["places"][0]["comments"],
                ["Room for Bowie", "Water tap near the gate"],
            )
            self.assertEqual(
                [(c["id"], c["average_rating"]) for c in cafes["places"]],
                [(cafe_id, 4.5)],
            )
