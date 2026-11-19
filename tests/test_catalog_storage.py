import asyncio
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import boto3
from botocore.response import StreamingBody
from botocore.stub import Stubber
from fastmcp import Client

from mcp_server import Catalog, Park, Rating, catalog_s3, mcp, read_catalog

OBJECT = {"Bucket": "test-catalog", "Key": "bowie/places.json"}


def json_bytes(catalog: Catalog) -> bytes:
    return (
        catalog.model_dump_json(
            indent=2, exclude_none=True, exclude_computed_fields=True
        )
        + "\n"
    ).encode()


def object_response(catalog: Catalog, etag: str) -> dict[str, object]:
    contents = json_bytes(catalog)
    return {"Body": StreamingBody(BytesIO(contents), len(contents)), "ETag": etag}


class S3CatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        client = boto3.client(
            "s3",
            region_name="ap-southeast-2",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        self.addCleanup(client.close)
        self.stub = self.enterContext(Stubber(client))
        self.enterContext(patch("mcp_server.boto3.client", return_value=client))
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "CATALOG_STORAGE": "s3",
                    "CATALOG_S3_BUCKET": OBJECT["Bucket"],
                    "CATALOG_S3_KEY": "",
                },
            )
        )
        catalog_s3.cache_clear()
        self.addCleanup(catalog_s3.cache_clear)
        self.catalog = Catalog(
            places=[
                Park(
                    id="park",
                    type="park",
                    name="Test Park",
                    suburb="Holland Park",
                    country="Australia",
                )
            ]
        )
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "places.json"
        path.write_bytes(json_bytes(self.catalog))
        self.enterContext(patch("mcp_server.CATALOG_PATH", path))

    def test_seeds_a_missing_object_without_replacing_another_seed(self) -> None:
        self.stub.add_client_error(
            "get_object", "NoSuchKey", http_status_code=404, expected_params=OBJECT
        )
        self.stub.add_client_error(
            "put_object",
            "PreconditionFailed",
            http_status_code=412,
            expected_params={
                **OBJECT,
                "Body": json_bytes(self.catalog),
                "ContentType": "application/json",
                "IfNoneMatch": "*",
            },
        )
        existing = Catalog(places=[])
        self.stub.add_response(
            "get_object", object_response(existing, '"winner"'), OBJECT
        )

        self.assertEqual(read_catalog(initialize_s3=True), (existing, '"winner"'))
        self.stub.assert_no_pending_responses()

    def test_round_trip_and_conflicting_review(self) -> None:
        updated = self.catalog.model_copy(deep=True)
        updated.places[0].ratings.append(Rating(stars=4))
        self.stub.add_response(
            "get_object", object_response(self.catalog, '"original"'), OBJECT
        )
        self.stub.add_response(
            "put_object",
            {},
            {
                **OBJECT,
                "Body": json_bytes(updated),
                "ContentType": "application/json",
                "IfMatch": '"original"',
            },
        )
        self.stub.add_response(
            "get_object", object_response(updated, '"updated"'), OBJECT
        )
        conflict = updated.model_copy(deep=True)
        conflict.places[0].ratings.append(Rating(stars=1))
        self.stub.add_client_error(
            "put_object",
            "PreconditionFailed",
            http_status_code=412,
            expected_params={
                **OBJECT,
                "Body": json_bytes(conflict),
                "ContentType": "application/json",
                "IfMatch": '"updated"',
            },
        )
        self.stub.add_response(
            "get_object", object_response(updated, '"updated"'), OBJECT
        )

        async def exercise_tools() -> None:
            async with Client(mcp) as client:
                args = {"place_id": "park", "stars": 4}
                result = await client.call_tool("review_dog_park", args)
                assert result.structured_content is not None
                self.assertEqual(result.structured_content["average_rating"], 4)
                blocked = await client.call_tool(
                    "review_dog_park", {**args, "stars": 1}, raise_on_error=False
                )
                self.assertTrue(blocked.is_error)
                self.assertIn("The catalog changed", str(blocked.content))
                listed = await client.call_tool("find_dog_parks")
                assert listed.structured_content is not None
                self.assertEqual(
                    [
                        r["stars"]
                        for r in listed.structured_content["places"][0]["ratings"]
                    ],
                    [4],
                )

        asyncio.run(exercise_tools())
        self.stub.assert_no_pending_responses()
