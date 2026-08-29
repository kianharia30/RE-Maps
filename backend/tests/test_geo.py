"""Bounding-box parsing and validation (§30)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.geo import BoundingBox


class TestParsing:
    def test_valid(self):
        box = BoundingBox.parse("-0.78,52.03,-0.75,52.05")
        assert box.west == -0.78 and box.north == 52.05

    @pytest.mark.parametrize(
        "raw",
        [
            "", "1,2,3", "1,2,3,4,5", "a,b,c,d", "-0.78,52.03,-0.75",
            "-0.78;52.03;-0.75;52.05",
        ],
    )
    def test_malformed_is_rejected(self, raw):
        with pytest.raises(ValueError):
            BoundingBox.parse(raw)

    def test_out_of_range_is_rejected(self):
        with pytest.raises(ValidationError):
            BoundingBox.parse("-200,52,-0.75,53")
        with pytest.raises(ValidationError):
            BoundingBox.parse("-1,-95,1,95")

    def test_inverted_latitudes_are_rejected(self):
        with pytest.raises(ValidationError):
            BoundingBox.parse("-1,53,1,52")

    def test_degenerate_longitude_is_rejected(self):
        with pytest.raises(ValidationError):
            BoundingBox.parse("1,52,1,53")


class TestGeometry:
    def test_centre(self):
        lat, lon = BoundingBox.parse("-2,50,0,52").centre
        assert (lat, lon) == (51.0, -1.0)

    def test_antimeridian_crossing_is_detected(self):
        box = BoundingBox.parse("170,-10,-170,10")
        assert box.crosses_antimeridian
        lat, lon = box.centre
        assert lat == 0.0
        # The centre must land at 180, not at 0 (which is the naive average).
        assert abs(abs(lon) - 180.0) < 1e-9

    def test_non_crossing_box_is_not_flagged(self):
        assert not BoundingBox.parse("-2,50,0,52").crosses_antimeridian

    def test_clamped_limits_to_mercator_range(self):
        box = BoundingBox(west=-1, south=-89.9, east=1, north=89.9).clamped()
        assert box.south >= -85.06 and box.north <= 85.06
        # Longitude is untouched.
        assert box.west == -1 and box.east == 1
