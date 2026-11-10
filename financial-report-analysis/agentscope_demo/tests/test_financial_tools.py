import asyncio
import json

import pytest

from mock_source import build_payload
from tools import financial_tools
from tools.financial_tools import FinancialToolbox


@pytest.fixture
def toolbox(monkeypatch):
    payload = build_payload()
    monkeypatch.setattr(financial_tools, "fetch_company", lambda _: payload["company"])
    monkeypatch.setattr(financial_tools, "fetch_financial_values", lambda *_: payload["records"])
    return FinancialToolbox("DEMO001")


def call(toolbox, name, period):
    return json.loads(asyncio.run(getattr(toolbox, name)(period)))


@pytest.mark.parametrize("period", ["2025", ' "2025" ', "'2025'", 2025])
def test_quoted_period_works_through_all_tools(toolbox, period):
    query = call(toolbox, "query_financial_data", period)
    assert query["status"] == "success"
    assert query["periods"] == ["2022", "2023", "2024", "2025"]
    calculated = call(toolbox, "calculate_financial_indicators", period)
    assert calculated["status"] == "success"
    assert set(calculated["indicators"]) == set(query["periods"])
    quality = call(toolbox, "get_data_quality_summary", period)
    assert quality["status"] == "success"
    assert set(quality["period_quality"]) == {"2025"}
    assert quality["period_quality"]["2025"]["record_count"] > 0
    assert quality["period_quality"]["2025"]["audited"] is False
    assert all(event["report_period"] == "2025" for event in toolbox.audit_events)
    assert toolbox.audit_events[-1]["periods"] == ["2025"]


@pytest.mark.parametrize("name", ["query_financial_data", "calculate_financial_indicators", "get_data_quality_summary"])
def test_missing_period_is_not_success(toolbox, name):
    call(toolbox, "query_financial_data", "")
    result = call(toolbox, name, '"2099"')
    assert result["status"] == "not_found"
    assert result["available_periods"] == ["2022", "2023", "2024", "2025"]
    assert toolbox.audit_events[-1]["status"] == "not_found"


@pytest.mark.parametrize("name", ["query_financial_data", "calculate_financial_indicators", "get_data_quality_summary"])
@pytest.mark.parametrize("period", ["2025年", "20x5", None, True])
def test_invalid_period_is_error(toolbox, name, period):
    result = call(toolbox, name, period)
    assert result["status"] == "error"
    assert toolbox.audit_events[-1]["status"] == "error"


def test_empty_period_checks_all_years(toolbox):
    call(toolbox, "query_financial_data", "")
    result = call(toolbox, "get_data_quality_summary", "")
    assert set(result["period_quality"]) == {"2022", "2023", "2024", "2025"}


def test_quality_without_query_is_error(toolbox):
    result = call(toolbox, "get_data_quality_summary", "2025")
    assert result["status"] == "error"
    assert toolbox.audit_events[-1]["status"] == "error"
