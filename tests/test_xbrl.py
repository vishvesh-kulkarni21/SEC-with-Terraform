from datetime import date

import pytest

from equity_research.data.xbrl import MissingMetricError, annual_facts, fiscal_year_of


def raw(val, start, end, filed, accn="A1", form="10-K", fy=2024, fp="FY"):
    return {"val": val, "start": start, "end": end, "filed": filed, "accn": accn,
            "form": form, "fy": fy, "fp": fp}


def companyfacts(tags: dict[str, list[dict]]) -> dict:
    return {"facts": {"us-gaap": {t: {"units": {"USD": rows}} for t, rows in tags.items()}}}


def test_keeps_only_annual_10k_durations():
    cf = companyfacts({"Revenues": [
        raw(100, "2023-01-01", "2023-12-31", "2024-02-01"),               # annual 10-K: keep
        raw(30, "2023-10-01", "2023-12-31", "2024-02-01"),                # Q4 inside the 10-K: drop
        raw(70, "2023-01-01", "2023-09-30", "2023-11-01", form="10-Q"),   # 10-Q: drop
    ]})
    facts = annual_facts(cf, "revenue")
    assert list(facts) == [2023]
    assert facts[2023].value == 100


def test_latest_filing_wins_for_restated_period():
    cf = companyfacts({"Revenues": [
        raw(93_775, "2021-01-04", "2022-01-02", "2022-02-17", accn="orig"),
        raw(78_740, "2021-01-04", "2022-01-02", "2024-02-16", accn="restated"),
    ]})
    fact = annual_facts(cf, "revenue")[2021]
    assert fact.value == 78_740
    assert fact.accn == "restated"


def test_latest_filing_wins_across_tags():
    # Old 10-K used Revenues; newer 10-K reports the same period under the ASC 606 tag.
    cf = companyfacts({
        "Revenues": [raw(265, "2017-10-01", "2018-09-29", "2018-11-05", accn="old")],
        "RevenueFromContractWithCustomerExcludingAssessedTax": [
            raw(266, "2017-10-01", "2018-09-29", "2020-10-30", accn="new")],
    })
    fact = annual_facts(cf, "revenue")[2018]
    assert (fact.value, fact.tag) == (266, "RevenueFromContractWithCustomerExcludingAssessedTax")


def test_tag_priority_breaks_ties_within_one_filing():
    cf = companyfacts({
        "Revenues": [raw(500, "2023-01-01", "2023-12-31", "2024-02-01")],
        "RevenueFromContractWithCustomerExcludingAssessedTax": [
            raw(450, "2023-01-01", "2023-12-31", "2024-02-01")],
    })
    assert annual_facts(cf, "revenue")[2023].tag == "Revenues"


def test_fy_field_is_ignored():
    # A FY2024 10-K reports the FY2022 comparative with fy=2024; it must land in 2022.
    cf = companyfacts({"NetIncomeLoss": [raw(10, "2021-10-01", "2022-09-30", "2024-11-01", fy=2024)]})
    assert list(annual_facts(cf, "net_income")) == [2022]


def test_53_week_year_is_annual():
    cf = companyfacts({"NetIncomeLoss": [raw(10, "2022-09-25", "2023-09-30", "2023-11-03")]})  # 371 days
    assert list(annual_facts(cf, "net_income")) == [2023]


def test_missing_metric_raises():
    with pytest.raises(MissingMetricError):
        annual_facts(companyfacts({}), "revenue")


@pytest.mark.parametrize("end,label", [
    (date(2025, 9, 27), 2025),   # Apple, September year end
    (date(2025, 6, 30), 2025),   # Microsoft, June
    (date(2023, 1, 1), 2022),    # J&J 52/53-week year ending in early January
    (date(2026, 1, 31), 2026),   # Walmart names the year after its January end
])
def test_fiscal_year_label(end, label):
    assert fiscal_year_of(end) == label


def test_fact_carries_source():
    cf = companyfacts({"NetIncomeLoss": [raw(10, "2023-01-01", "2023-12-31", "2024-02-01", accn="0001-24-1")]})
    src = annual_facts(cf, "net_income")[2023].source
    assert "NetIncomeLoss" in src and "0001-24-1" in src and "2023-12-31" in src
