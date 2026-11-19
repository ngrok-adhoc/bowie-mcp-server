from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from functools import cache
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import TYPE_CHECKING, Annotated, Literal, TypeVar
from uuid import uuid4

import boto3
import uvicorn
from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StrictBool,
    StrictStr,
    StringConstraints,
    TypeAdapter,
    computed_field,
    model_validator,
)

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

DATA_PATH = Path(__file__).resolve().parent / "data" / "places.json"
HOST = "127.0.0.1"
# Queensland Place Names Register, record 49443.
HOLLAND_PARK_LAT, HOLLAND_PARK_LNG = map(radians, (-27.51944, 153.07278))

# shortcut: local JSON supports one server process. Use S3 for shared storage.
data_lock = Lock()
mcp = FastMCP(
    "Bowie",
    instructions=(
        "Find, add, and review dog parks and dog-friendly cafés with Bowie. "
        "For nearby searches around Holland Park, use radius_km without suburb "
        "so results can cross suburb boundaries. "
        "Use public_ratings for published scores and attribute the platform. "
        "The average_rating field covers reviews submitted to Bowie."
    ),
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Stars = Annotated[
    int, Field(strict=True, ge=1, le=5, description="One to five whole stars.")
]
PlaceId = Annotated[
    Name, Field(description="The saved place ID returned by its find or add tool.")
]
SuburbFilter = Annotated[
    Name, Field(description="Case-insensitive substring of the suburb or locality.")
]
CountryFilter = Annotated[
    Name, Field(description="Case-insensitive full country name.")
]
RadiusKm = Annotated[
    float,
    Field(
        strict=True,
        gt=0,
        allow_inf_nan=False,
        description="Straight-line radius in kilometres from Holland Park, Brisbane.",
    ),
]


class PlaceDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    suburb: Name = Field(description="Suburb or equivalent locality.")
    country: Name
    address: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    phone: str | None = Field(
        default=None, description="Public business telephone number."
    )
    website: HttpUrl | None = None
    description: str | None = Field(
        default=None,
        description="Free-text details about the place, such as shade, water, seating, and when it gets busy.",
        validation_alias=AliasChoices("description", "notes"),
    )
    sources: list[HttpUrl] | None = Field(
        default=None, description="Sources for the supplied details."
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Straight-line distance from Holland Park, Brisbane, in kilometres."
    )
    @property
    def distance_from_holland_park_km(self) -> float | None:
        if self.latitude is None or self.longitude is None:
            return None
        lat, lng = radians(self.latitude), radians(self.longitude)
        # Haversine distance using Earth's mean radius.
        a = (
            sin((lat - HOLLAND_PARK_LAT) / 2) ** 2
            + cos(HOLLAND_PARK_LAT) * cos(lat) * sin((lng - HOLLAND_PARK_LNG) / 2) ** 2
        )
        return 2 * 6371.0088 * asin(sqrt(min(a, 1.0)))


class ParkDetails(PlaceDetails):
    drinking_water: StrictBool | None = Field(
        default=None, description="Drinking water in the dog area, if known."
    )


class CafeDetails(PlaceDetails):
    dog_access: Literal["outdoor", "outdoor_only", "indoor"] | None = Field(
        default=None,
        description="Use outdoor_only only when indoor access is explicitly restricted.",
    )
    puppuccino: StrictBool | None = Field(
        default=None, description="Whether the café serves puppuccinos, if known."
    )


class Rating(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stars: Stars
    comment: str | None = None
    source: HttpUrl | None = Field(
        default=None, description="Public source URL for a reported observation."
    )


class PublicRating(BaseModel):
    stars: float = Field(gt=0, le=5)
    review_count: int = Field(gt=0)
    platform: str
    scope: Literal["venue", "whole_park", "dog_enclosure"]
    source: HttpUrl | None = None


class ResearchNote(BaseModel):
    text: str
    kind: Literal["review", "review_summary", "listing", "venue", "agent_report"]
    source: HttpUrl | None = None


class RatedPlace(BaseModel):
    ratings: list[Rating] = Field(default_factory=list)
    public_ratings: list[PublicRating] = Field(
        default_factory=list,
        description="Published aggregate scores, separate from submitted reviews.",
    )
    research_notes: list[ResearchNote] = Field(default_factory=list)

    @computed_field(description="Average of the saved star values, if any.")
    def average_rating(self) -> float | None:
        if not self.ratings:
            return None
        return sum(rating.stars for rating in self.ratings) / len(self.ratings)

    @computed_field(
        description="Comments from submitted reviews and summaries of public reviews."
    )
    def comments(self) -> list[str]:
        return [
            rating.comment
            for rating in self.ratings
            if rating.comment and rating.comment.strip()
        ] + [
            note.text
            for note in self.research_notes
            if note.kind in ("review", "review_summary")
        ]

    @model_validator(mode="before")
    @classmethod
    def read_legacy_rating(cls, value: object) -> object:
        if isinstance(value, dict) and "rating" in value:
            value = value.copy()
            legacy = value.pop("rating")
            if legacy is not None:
                ratings = value.get("ratings", [])
                if not isinstance(ratings, list):
                    raise ValueError("Ratings must be a list.")
                value["ratings"] = [legacy, *ratings]
        return value


class Park(ParkDetails, RatedPlace):
    id: PlaceId
    type: Literal["park"]


class Cafe(CafeDetails, RatedPlace):
    id: PlaceId
    type: Literal["cafe"]


PlaceT = TypeVar("PlaceT", Park, Cafe)


class PlaceCollection(BaseModel):
    model_config = ConfigDict(extra="allow")

    places: list[Annotated[Park | Cafe, Field(discriminator="type")]]


class ParkMatches(BaseModel):
    places: list[Park]


class CafeMatches(BaseModel):
    places: list[Cafe]


@cache
def s3_storage() -> tuple[S3Client, str, str] | None:
    storage = os.environ.get("STORAGE", "local")
    if storage == "local":
        return None
    if storage != "s3":
        raise ValueError("STORAGE must be local or s3.")
    bucket = os.environ.get("S3_BUCKET", "").strip()
    if not bucket:
        raise ValueError("Set S3_BUCKET when STORAGE=s3.")
    key = os.environ.get("S3_KEY", "").strip() or "bowie/places.json"
    return boto3.client("s3"), bucket, key


def read_collection(
    *, initialize_s3: bool = False
) -> tuple[PlaceCollection, str | None]:
    s3 = s3_storage()
    if s3 is None:
        return PlaceCollection.model_validate_json(DATA_PATH.read_bytes()), None

    client, bucket, key = s3
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except client.exceptions.NoSuchKey:
        if not initialize_s3:
            raise
        seed = DATA_PATH.read_bytes()
        PlaceCollection.model_validate_json(seed)
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=seed,
                ContentType="application/json",
                IfNoneMatch="*",
            )
        except ClientError as error:
            if error.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise
        response = client.get_object(Bucket=bucket, Key=key)

    with closing(response["Body"]) as body:
        return PlaceCollection.model_validate_json(body.read()), response["ETag"]


def save_collection(collection: PlaceCollection, etag: str | None) -> None:
    contents = (
        collection.model_dump_json(
            indent=2, exclude_none=True, exclude_computed_fields=True
        )
        + "\n"
    )
    s3 = s3_storage()
    if s3 is not None:
        if etag is None:
            raise RuntimeError("An ETag is required to update the S3 data.")
        client, bucket, key = s3
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=contents.encode("utf-8"),
                ContentType="application/json",
                IfMatch=etag,
            )
        except ClientError as error:
            if error.response["ResponseMetadata"]["HTTPStatusCode"] in (409, 412):
                raise ToolError(
                    "The saved data changed during this edit. Retry the operation."
                ) from error
            raise
        return

    temp = NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=DATA_PATH.parent, delete=False
    )
    temp_path = Path(temp.name)
    try:
        with temp:
            temp.write(contents)
        temp_path.replace(DATA_PATH)
    finally:
        temp_path.unlink(missing_ok=True)


def matches_location(
    place: PlaceDetails, suburb: str | None, country: str | None
) -> bool:
    return (suburb is None or suburb.casefold() in place.suburb.casefold()) and (
        country is None or country.casefold() == place.country.casefold()
    )


def by_distance(places: list[PlaceT], radius_km: float | None) -> list[PlaceT]:
    if radius_km is None:
        return places
    nearby: list[tuple[float, PlaceT]] = []
    for place in places:
        distance = place.distance_from_holland_park_km
        if distance is not None and distance <= radius_km:
            nearby.append((distance, place))
    nearby.sort(key=lambda match: match[0])
    return [place for _, place in nearby]


@mcp.tool(annotations={"read_only_hint": True, "open_world_hint": False})
def find_dog_parks(
    suburb: SuburbFilter | None = None,
    country: CountryFilter | None = None,
    drinking_water: StrictBool | None = None,
    radius_km: RadiusKm | None = None,
) -> ParkMatches:
    """Find saved dog parks, with IDs, descriptions, and reviews. Omit filters to list all.

    Use radius_km for nearest-first results around Holland Park, Brisbane. Omit suburb
    to include adjacent suburbs. Radius searches require saved coordinates.
    The drinking-water filter matches only an explicit yes or no.
    """
    with data_lock:
        collection, _ = read_collection()
    return ParkMatches(
        places=by_distance(
            [
                place
                for place in collection.places
                if isinstance(place, Park)
                and matches_location(place, suburb, country)
                and (drinking_water is None or place.drinking_water is drinking_water)
            ],
            radius_km,
        )
    )


@mcp.tool(annotations={"read_only_hint": True, "open_world_hint": False})
def find_dog_cafes(
    suburb: SuburbFilter | None = None,
    country: CountryFilter | None = None,
    puppuccino: StrictBool | None = None,
    radius_km: RadiusKm | None = None,
) -> CafeMatches:
    """Find saved dog-friendly cafés, with IDs, contact details, descriptions, and reviews.

    Omit filters to list all. Use radius_km for nearest-first results around Holland Park,
    Brisbane. Omit suburb to include adjacent suburbs. Radius searches require saved
    coordinates. The puppuccino filter matches only an explicit yes or no.
    """
    with data_lock:
        collection, _ = read_collection()
    return CafeMatches(
        places=by_distance(
            [
                place
                for place in collection.places
                if isinstance(place, Cafe)
                and matches_location(place, suburb, country)
                and (puppuccino is None or place.puppuccino is puppuccino)
            ],
            radius_km,
        )
    )


def add_place(place: Park | Cafe) -> None:
    with data_lock:
        collection, etag = read_collection()
        collection.places.append(place)
        save_collection(collection, etag)


@mcp.tool(
    annotations={
        "destructive_hint": False,
        "idempotent_hint": False,
        "open_world_hint": False,
    }
)
def add_dog_park(place: ParkDetails) -> Park:
    """Add a dog park from details you supply. Saves a new place and returns its stable ID."""
    saved = Park(
        id=uuid4().hex, type="park", **place.model_dump(exclude_computed_fields=True)
    )
    add_place(saved)
    return saved


@mcp.tool(
    annotations={
        "destructive_hint": False,
        "idempotent_hint": False,
        "open_world_hint": False,
    }
)
def add_dog_cafe(place: CafeDetails) -> Cafe:
    """Add a dog-friendly café from details you supply. Saves a new place and returns its stable ID."""
    saved = Cafe(
        id=uuid4().hex, type="cafe", **place.model_dump(exclude_computed_fields=True)
    )
    add_place(saved)
    return saved


def append_review(kind: type[PlaceT], place_id: str, rating: Rating) -> PlaceT:
    with data_lock:
        collection, etag = read_collection()
        for place in collection.places:
            if isinstance(place, kind) and place.id == place_id:
                place.ratings.append(rating)
                save_collection(collection, etag)
                return place
    raise ToolError(f"No saved {kind.__name__.lower()} found for ID {place_id!r}.")


@mcp.tool(
    annotations={
        "destructive_hint": False,
        "idempotent_hint": False,
        "open_world_hint": False,
    }
)
def review_dog_park(
    place_id: PlaceId,
    stars: Stars,
    comment: str | None = None,
    source: HttpUrl | None = None,
) -> Park:
    """Submit a park review with one to five stars and an optional comment and source URL."""
    return append_review(
        Park, place_id, Rating(stars=stars, comment=comment, source=source)
    )


@mcp.tool(
    annotations={
        "destructive_hint": False,
        "idempotent_hint": False,
        "open_world_hint": False,
    }
)
def review_dog_cafe(
    place_id: PlaceId,
    stars: Stars,
    comment: str | None = None,
    source: HttpUrl | None = None,
) -> Cafe:
    """Submit a café review with one to five stars and an optional comment and source URL."""
    return append_review(
        Cafe, place_id, Rating(stars=stars, comment=comment, source=source)
    )


mcp_http = mcp.http_app(
    path="/mcp",
    json_response=True,
    host_origin_protection=True,
    allowed_hosts=[
        HOST,
        *TypeAdapter(list[StrictStr]).validate_json(
            os.environ.get("ALLOWED_HOSTS", "[]")
        ),
    ],
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    read_collection(initialize_s3=True)
    async with mcp_http.lifespan(app):
        yield


app = FastAPI(lifespan=lifespan, openapi_url=None)
app.mount("/", mcp_http)


def main(port: int = 3001) -> None:
    uvicorn.run(app, host=os.environ.get("BIND_HOST", HOST), port=port)


if __name__ == "__main__":
    main()
