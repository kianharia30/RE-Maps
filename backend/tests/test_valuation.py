"""Comparable scoring, robust statistics and confidence grading (§19, §22)."""
from __future__ import annotations

from datetime import date

from app.models.enums import Confidence
from app.valuation import comparables as cmp
from app.valuation.avm import _weighted_dispersion, _weighted_quantile
from app.valuation.confidence import (
    INTERVAL_BY_CONFIDENCE,
    ConfidenceInputs,
    grade,
)


def _comp(distance_m=200.0, ptype="semi_detached", sold=date(2024, 6, 1), area=None):
    return cmp.RawComparable(
        transaction_id=1, property_id=1, price=300_000.0, sold_date=sold,
        property_type=ptype, floor_area_sqm=area, distance_m=distance_m,
        latitude=52.0, longitude=-0.75, address_short="1 Test Road",
        postcode="MK92AB", district="Milton Keynes",
    )


class TestSimilarity:
    AS_OF = date(2024, 6, 1)

    def test_closer_scores_higher(self):
        near = cmp.score(_comp(distance_m=100), target_type="semi_detached",
                         target_area=None, as_of=self.AS_OF)
        far = cmp.score(_comp(distance_m=5000), target_type="semi_detached",
                        target_area=None, as_of=self.AS_OF)
        assert near > far

    def test_same_type_scores_higher(self):
        same = cmp.score(_comp(ptype="semi_detached"), target_type="semi_detached",
                         target_area=None, as_of=self.AS_OF)
        different = cmp.score(_comp(ptype="flat"), target_type="semi_detached",
                              target_area=None, as_of=self.AS_OF)
        assert same > different

    def test_a_flat_is_weak_evidence_for_a_detached_house(self):
        """This is the mismatch that inflated intervals before the type
        restriction and similarity floor were added."""
        score = cmp.score(_comp(ptype="flat", distance_m=100),
                          target_type="detached", target_area=None, as_of=self.AS_OF)
        from app.valuation.avm import MIN_SIMILARITY

        assert score < MIN_SIMILARITY, "must be discarded, not merely down-weighted"

    def test_recent_scores_higher(self):
        recent = cmp.score(_comp(sold=date(2024, 5, 1)), target_type="semi_detached",
                           target_area=None, as_of=self.AS_OF)
        old = cmp.score(_comp(sold=date(2019, 5, 1)), target_type="semi_detached",
                        target_area=None, as_of=self.AS_OF)
        assert recent > old

    def test_similar_size_scores_higher_when_area_is_known(self):
        similar = cmp.score(_comp(area=95), target_type="semi_detached",
                            target_area=100, as_of=self.AS_OF)
        different = cmp.score(_comp(area=250), target_type="semi_detached",
                              target_area=100, as_of=self.AS_OF)
        assert similar > different

    def test_missing_area_does_not_penalise(self):
        """A missing feature must renormalise the weights, not act as a zero —
        otherwise every UK property (no EPC key) would score badly."""
        without = cmp.score(_comp(area=None), target_type="semi_detached",
                            target_area=None, as_of=self.AS_OF)
        assert without > 0.5

    def test_score_is_bounded(self):
        for d in (0, 50, 500, 5000, 50_000):
            for t in ("semi_detached", "flat", "detached", None):
                s = cmp.score(_comp(distance_m=d, ptype=t),
                              target_type="semi_detached", target_area=None,
                              as_of=self.AS_OF)
                assert 0.0 <= s <= 1.0

    def test_house_type_is_interchangeable_with_specific_house_types(self):
        """France records only Maison/Appartement, so `house` must be strong
        evidence for a detached/semi/terraced target and weak for a flat."""
        assert cmp._type_score("detached", "house") > 0.7
        assert cmp._type_score("flat", "house") < 0.3


class TestRobustStatistics:
    def test_weighted_quantile_matches_the_unweighted_median(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        weights = [1.0] * 5
        assert _weighted_quantile(values, weights, 0.5) == 3.0

    def test_weight_moves_the_median(self):
        values = [1.0, 2.0, 3.0, 100.0]
        assert _weighted_quantile(values, [1, 1, 1, 50], 0.5) == 100.0
        assert _weighted_quantile(values, [50, 1, 1, 1], 0.5) == 1.0

    def test_dispersion_ignores_a_single_outlier(self):
        """An IQR-based measure must not be inflated by one extreme sale."""
        tight = [1.0, 1.05, 1.1, 1.12, 1.15, 1.2, 1.22, 1.25]
        weights = [1.0] * len(tight)
        base = _weighted_dispersion(tight, weights)
        with_outlier = _weighted_dispersion([*tight, 9.0], [*weights, 1.0])
        assert with_outlier < base * 2.0

    def test_dispersion_is_zero_for_identical_values(self):
        assert _weighted_dispersion([2.0] * 6, [1.0] * 6) == 0.0

    def test_dispersion_handles_tiny_samples(self):
        assert _weighted_dispersion([1.0, 2.0, 3.0], [1, 1, 1]) >= 0.0


class TestConfidence:
    STRONG = ConfidenceInputs(
        comparable_count=20, effective_count=15.0, median_distance_m=250,
        dispersion=0.10, top_similarity=0.9, months_to_nearest_evidence=3,
        adjustment_level="local_authority", own_sale_years_ago=2.0,
        horizon_years=0.0,
    )

    def test_strong_evidence_grades_high(self):
        level, reasons = grade(self.STRONG)
        assert level is Confidence.HIGH
        assert reasons

    def test_sparse_evidence_grades_low_or_worse(self):
        weak = ConfidenceInputs(
            comparable_count=3, effective_count=1.8, median_distance_m=4000,
            dispersion=0.45, top_similarity=0.4, months_to_nearest_evidence=48,
            adjustment_level="country", own_sale_years_ago=None,
            horizon_years=0.0,
        )
        level, reasons = grade(weak)
        assert level in (Confidence.LOW, Confidence.VERY_LOW)
        assert any("few comparable" in r for r in reasons)

    def test_grade_is_monotonic_in_evidence_volume(self):
        import dataclasses

        levels = []
        order = [Confidence.VERY_LOW, Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH]
        for count, effective in [(2, 1.0), (4, 2.5), (8, 6.5), (25, 18.0)]:
            level, _ = grade(
                dataclasses.replace(
                    self.STRONG, comparable_count=count, effective_count=effective
                )
            )
            levels.append(order.index(level))
        assert levels == sorted(levels)

    def test_longer_horizon_lowers_confidence(self):
        import dataclasses

        order = [Confidence.VERY_LOW, Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH]
        near, _ = grade(dataclasses.replace(self.STRONG, horizon_years=1.0))
        far, _ = grade(dataclasses.replace(self.STRONG, horizon_years=10.0))
        assert order.index(far) <= order.index(near)

    def test_coarse_index_is_penalised_and_explained(self):
        import dataclasses

        _level, reasons = grade(
            dataclasses.replace(self.STRONG, adjustment_level="country")
        )
        assert any("national price index" in r for r in reasons)

    def test_interval_widens_as_confidence_falls(self):
        widths = [
            INTERVAL_BY_CONFIDENCE[c]
            for c in (Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW,
                      Confidence.VERY_LOW)
        ]
        assert widths == sorted(widths)
