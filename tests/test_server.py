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
    env.pop("BOWIE_STORAGE", None)
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


def project_with(places: list[dict[str, Any]], directory: Path) -> Path:
    seed = json.loads((ROOT / "data" / "places.json").read_bytes())
    seed["places"] = places
    (directory / "data").mkdir()
    (directory / "data" / "places.json").write_text(json.dumps(seed))
    (directory / "mcp_server.py").write_bytes((ROOT / "mcp_server.py").read_bytes())
    return directory


def seed_places() -> list[dict[str, Any]]:
    seed = json.loads((ROOT / "data" / "places.json").read_bytes())
    places: list[dict[str, Any]] = seed["places"]
    return places


class BowieLimitsTest(unittest.TestCase):
    def test_paging_and_input_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = project_with(seed_places(), Path(temp))
            with running_server(project) as url:
                asyncio.run(self.check_paging_and_limits(url))

    async def rejected(
        self, client: Client[Any], tool: str, arguments: dict[str, Any]
    ) -> None:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
        self.assertTrue(result.is_error, arguments)

    async def check_paging_and_limits(self, url: str) -> None:
        async with Client(url) as client:
            everything = await call(client, "find_dog_parks")
            page = await call(client, "find_dog_parks", {"limit": 2, "offset": 1})
            self.assertEqual(page["total"], everything["total"])
            self.assertEqual(
                [p["id"] for p in page["places"]],
                [p["id"] for p in everything["places"][1:3]],
            )
            for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
                await self.rejected(client, "find_dog_cafes", bad)

            place = {"suburb": "Bowie Test", "country": "Australia", "name": "Limits"}
            for field, value in (
                ("name", "n" * 201),
                ("description", "d" * 2001),
                ("address", "a" * 301),
                ("phone", "1" * 301),
                ("sources", [f"https://example.com/{i}" for i in range(11)]),
                ("latitude", -27.5),
                ("name", "Hidden\U000e0041 text"),
                ("description", "Reversed \u202e text"),
                ("description", "Null \x00 byte"),
                ("description", "Zero\u200bwidth"),
                ("description", "Tags \U000e0041"),
                ("name", "Two\nlines"),
            ):
                await self.rejected(
                    client, "add_dog_park", {"place": {**place, field: value}}
                )
            await self.rejected(
                client,
                "review_dog_park",
                {"place_id": "x", "stars": 3, "comment": "c" * 2001},
            )
            # Emoji joiners and line breaks are legitimate text.
            ok = await call(
                client,
                "add_dog_park",
                {
                    "place": {
                        **place,
                        "name": "Dog \U0001f415\u200d\U0001f9ba Park",
                        "description": "Shade.\nWater.",
                    }
                },
            )
            self.assertEqual(ok["description"], "Shade.\nWater.")
            for name in (
                "\u05e8\u05d7\u05d5\u05d1\u200f 5",
                "Wales \U0001f3f4\U000e0067\U000e0062\U000e0077\U000e006c\U000e0073\U000e007f",
            ):
                await call(client, "add_dog_park", {"place": {**place, "name": name}})

    def test_place_and_review_caps(self) -> None:
        places = seed_places()
        park = next(p for p in places if p["type"] == "park")
        park["ratings"] = [{"stars": 3} for _ in range(100)]
        clones = [{**park, "id": f"clone-{i}", "ratings": []} for i in range(500)]
        with tempfile.TemporaryDirectory() as temp:
            project = project_with([park, *clones[:498]], Path(temp))
            with running_server(project) as url:
                asyncio.run(self.check_caps(url, park["id"]))

    async def check_caps(self, url: str, full_park_id: str) -> None:
        async with Client(url) as client:
            await self.rejected(
                client, "review_dog_park", {"place_id": full_park_id, "stars": 5}
            )
            await call(
                client,
                "add_dog_cafe",
                {"place": {"name": "Last", "suburb": "Bowie", "country": "Australia"}},
            )
            await self.rejected(
                client,
                "add_dog_cafe",
                {"place": {"name": "One too many", "suburb": "B", "country": "A"}},
            )

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores chmod")
    def test_server_errors_do_not_leak_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = project_with(seed_places(), Path(temp))
            data_dir = project / "data"
            with running_server(project) as url:
                data_dir.chmod(0o555)
                try:
                    message = asyncio.run(self.failing_write(url))
                finally:
                    data_dir.chmod(0o755)
            self.assertNotIn(temp, message)
            self.assertNotIn("Permission denied", message)

    async def failing_write(self, url: str) -> str:
        async with Client(url) as client:
            result = await client.call_tool(
                "add_dog_park",
                {
                    "place": {
                        "name": "No disk",
                        "suburb": "Bowie",
                        "country": "Australia",
                    }
                },
                raise_on_error=False,
            )
            self.assertTrue(result.is_error)
            return " ".join(getattr(c, "text", "") for c in result.content)


class SeedDataTest(unittest.TestCase):
    def test_ratings_are_unique_and_sourced(self) -> None:
        places = seed_places()
        self.assertEqual(len({p["id"] for p in places}), len(places))
        for place in places:
            ratings = place.get("public_ratings", [])
            keys = [(r["platform"], r["scope"]) for r in ratings]
            self.assertEqual(len(keys), len(set(keys)), place["id"])
            self.assertTrue(all(r.get("source") for r in ratings), place["id"])
            for note in place.get("research_notes", []):
                self.assertNotEqual(note["kind"], "agent_report", place["id"])
