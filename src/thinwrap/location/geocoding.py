"""Geocoding (forward, reverse, autocomplete) operation DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

from .enums import PlaceDetailsInclude
from .latlng import LatLng
from .passthrough import Passthrough


@dataclass(frozen=True)
class GeocodeOptions:
    address: str
    language: Optional[str] = None
    #: Hard filter of ISO 3166-1 alpha-2 codes.
    country_filter: Optional[Sequence[str]] = None
    passthrough: Optional[Passthrough] = None


@dataclass(frozen=True)
class ReverseGeocodeOptions:
    location: LatLng
    language: Optional[str] = None
    passthrough: Optional[Passthrough] = None


@dataclass(frozen=True)
class AutocompleteOptions:
    input: str
    location: Optional[LatLng] = None
    radius: Optional[float] = None
    language: Optional[str] = None

    #: Groups this keystroke with the rest of one user interaction, closed by the
    #: ``place_details()`` call carrying the SAME value.
    #:
    #: Google-only here: Places Autocomplete is billed per SESSION when a token ties
    #: the keystroke requests to the details call that closes them, and per REQUEST
    #: when it does not — so omitting it on a keystroke-driven UI multiplies the bill
    #: by the number of characters typed. Google documents a v4 UUID.
    #:
    #: The wrapper holds no state and cannot correlate the calls, so generating and
    #: threading the value is the caller's job. Ignored by every other provider.
    #: (Mapbox needs the same concept but only on ``place_details()``, because its
    #: ``suggest`` leg generates its own token.)
    session_token: Optional[str] = None

    passthrough: Optional[Passthrough] = None

    #: Hard country filter, ISO 3166-1 alpha-2 (e.g. ``["IL", "PS"]``) — the same
    #: vocabulary as :attr:`GeocodeOptions.country_filter`.
    #:
    #: All five geocoders support one natively, so this is a base field rather than
    #: a passthrough concern. Each connector translates it into that vendor's own
    #: parameter: Google ``includedRegionCodes``, Mapbox ``country``, TomTom
    #: ``countrySet``, Esri ``countryCode``, HERE ``in=countryCode`` (alpha-3).
    #:
    #: Two provider behaviours are worth knowing. Google takes ccTLD codes rather
    #: than ISO — the two disagree on the United Kingdom (``GB`` → ``uk``), which the
    #: connector translates — and setting the filter also stops Google returning
    #: *query* predictions, so it changes which kinds of suggestion come back. HERE
    #: requires the filter to be accompanied by a ``location``.
    #:
    #: Trails ``passthrough`` rather than sitting beside the other filters the way
    #: ``GeocodeOptions`` orders it: inserting a field mid-dataclass would renumber
    #: every positional construction, which a minor release must not do.
    country_filter: Optional[list[str]] = None


@dataclass(frozen=True)
class Viewport:
    southwest: LatLng
    northeast: LatLng


@dataclass(frozen=True)
class GeocodeCandidate:
    formatted_address: str
    location: LatLng
    place_id: Optional[str] = None
    viewport: Optional[Viewport] = None


@dataclass(frozen=True)
class GeocodeResult:
    candidates: List[GeocodeCandidate]
    raw: Any = None


@dataclass(frozen=True)
class ReverseGeocodeResult:
    candidates: List[GeocodeCandidate]
    raw: Any = None


@dataclass(frozen=True)
class AutocompleteStructuredFormat:
    """The two display parts of an autocomplete prediction.

    ``secondary_text`` is optional because HERE's *query*-type suggestions have a
    title but no address at all — emitting an empty string there would be a
    fabricated value a UI would happily render as a blank second line.
    """

    main_text: str
    secondary_text: Optional[str] = None


@dataclass(frozen=True)
class AutocompletePrediction:
    description: str
    place_id: Optional[str] = None

    #: The prediction split into its primary and secondary parts, when the provider
    #: returns them as distinct fields.
    #:
    #: This is what lets a UI render the usual two-line suggestion — the place name
    #: above a greyed-out address — without guessing where to split
    #: ``description``. Splitting on the first comma is the workaround this
    #: replaces, and it breaks on names containing commas and on locales that order
    #: the address differently.
    #:
    #: **Never synthesized.** Present only when the provider supplies a genuinely
    #: distinct main part: Google (``structuredFormat.mainText``), Mapbox (``name``
    #: / ``place_formatted``), HERE (``title`` / ``address.label``), TomTom
    #: (``poi.name`` / ``address.freeformAddress`` — **absent for street/address
    #: results**, which have no ``poi.name``). Esri returns a single flat ``text``
    #: field and is the genuine gap.
    #:
    #: So ``None`` means "this provider/row has no distinct main part", and
    #: ``description`` remains the thing to render.
    structured_format: Optional[AutocompleteStructuredFormat] = None


@dataclass(frozen=True)
class AutocompleteResult:
    predictions: List[AutocompletePrediction]
    raw: Any = None


@dataclass(frozen=True)
class PlaceDetailsOptions:
    """Input for a place-details lookup: resolve a ``place_id`` from an autocomplete
    prediction into a full candidate.

    This is deliberately ONE operation rather than two. "Place details" and "geocode
    by place id" are the same vendor call — every provider resolves its own opaque id
    to the same address+coordinates payload — so splitting them would put two names
    on one request.

    The ``place_id`` must come from the SAME provider's ``autocomplete()``: these ids
    are provider-scoped and not interchangeable.
    """

    place_id: str
    language: Optional[str] = None

    #: Closes a billable session opened by ``autocomplete()``. Honoured by Mapbox and
    #: Google; ignored by every other provider.
    #:
    #: Both vendors bill the autocomplete leg per *session* rather than per request,
    #: and a session is only one session when every call carries the SAME token:
    #:
    #: - Mapbox: a ``suggest`` call plus the ``retrieve`` that follows count as ONE
    #:   billable Search Box session. Sent as ``session_token``.
    #: - Google: the keystroke requests plus the details call that closes them count
    #:   as ONE session; without a token each keystroke is billed as its own request.
    #:   Sent as ``sessionToken``.
    #:
    #: Omitting it, or passing a fresh one, turns a single user interaction into
    #: several billed ones. The wrapper holds no state and cannot correlate the calls,
    #: so threading it is the caller's job.
    session_token: Optional[str] = None

    #: Optional output fields to fetch. Empty means nothing extra is requested.
    include: Sequence[PlaceDetailsInclude] = ()

    passthrough: Optional[Passthrough] = None

    def includes(self, token: PlaceDetailsInclude) -> bool:
        """Whether the caller opted into a given optional output field."""
        return token in self.include


@dataclass(frozen=True)
class PlaceDetailsResult:
    """Normalized place-details result.

    Returns a full :class:`GeocodeCandidate` rather than a new shape, because that is
    what the operation resolves to and reusing it means a consumer can feed the
    result straight into whatever already consumes geocode candidates.
    """

    candidate: GeocodeCandidate

    #: The place's display name, when the provider returns one distinct from the
    #: formatted address (e.g. "Blue Bottle Coffee" vs its street address).
    #:
    #: None on providers that only return an address (Esri), and on Google unless
    #: ``PlaceDetailsInclude.NAME`` was requested — its Place Details SKU tier is
    #: driven by the field mask, and ``displayName`` is a Pro-tier field. Note this
    #: is the OPPOSITE of Compute Routes, whose SKU is feature-driven: check per API,
    #: do not generalize.
    name: Optional[str] = None

    raw: Any = None
