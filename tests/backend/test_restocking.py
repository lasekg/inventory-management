"""
Tests for restocking API endpoints.
"""
from datetime import datetime

import pytest

import main


RANKED_SKUS = [
    "WDG-001", "GSK-203", "FLT-405", "BRG-102", "SNR-420",
    "CTL-330", "VLV-506", "PSU-501", "MTR-304",
]


def make_forecast(sku, current=100, forecast=100, trend="stable", unit_cost=10.0, lead_time_days=7):
    """Build a forecast record for logic tests that shouldn't depend on mock data."""
    return {
        "id": sku,
        "item_sku": sku,
        "item_name": f"Item {sku}",
        "current_demand": current,
        "forecasted_demand": forecast,
        "trend": trend,
        "period": "Next 30 days",
        "unit_cost": unit_cost,
        "lead_time_days": lead_time_days,
    }


def place_order(client, budget=42100, request_id="request-0001"):
    """Place a restocking order."""
    return client.post(
        "/api/restocking/orders",
        json={"budget": budget, "request_id": request_id},
    )


class TestRestockingRecommendations:
    """Test suite for the restocking recommendations endpoint."""

    def test_get_recommendations_structure(self, client):
        """Test that a plan has all contract fields."""
        response = client.get("/api/restocking/recommendations?budget=42100")
        assert response.status_code == 200

        plan = response.json()
        for field in ["budget", "items", "total_cost", "unspent", "lead_time_days",
                      "fully_funded", "unfunded_count", "budget_range"]:
            assert field in plan

        for field in ["sku", "name", "trend", "growth_pct", "target_quantity", "quantity",
                      "unit_cost", "line_total", "lead_time_days", "is_partial"]:
            assert field in plan["items"][0]

    def test_recommendations_golden_case(self, client):
        """Test the default budget: four full lines, then a partial line."""
        plan = client.get("/api/restocking/recommendations?budget=42100").json()

        assert [line["sku"] for line in plan["items"]] == RANKED_SKUS[:5]
        assert [line["is_partial"] for line in plan["items"]] == [False] * 4 + [True]
        assert plan["items"][-1]["quantity"] == 108
        assert plan["items"][-1]["target_quantity"] == 182
        assert plan["total_cost"] == 42058.50
        assert plan["unspent"] == 41.50
        assert plan["lead_time_days"] == 15
        assert plan["fully_funded"] is False
        assert plan["unfunded_count"] == 5

    def test_partial_line_counts_as_unfunded(self, monkeypatch):
        """Test that a partial last item is reported as not fully funded."""
        monkeypatch.setattr(main, "demand_forecasts", [make_forecast("A-1", forecast=10, unit_cost=10.0)])

        plan = main.build_restock_plan(50)

        assert plan["items"][0]["is_partial"] is True
        assert plan["unfunded_count"] == 1
        assert plan["fully_funded"] is False

    def test_budget_range_values(self, client):
        """Test that the slider range is derived from forecast costs."""
        plan = client.get("/api/restocking/recommendations").json()

        assert plan["budget_range"] == {"min": 100, "max": 84300, "step": 100, "default": 42100}

    def test_recommendations_default_budget(self, client):
        """Test that an omitted budget uses the default."""
        plan = client.get("/api/restocking/recommendations").json()

        assert plan["budget"] == 42100

    def test_recommendations_invariants_at_every_slider_step(self, client):
        """Test plan invariants across the full slider range."""
        budget_range = main.get_restock_budget_range()

        for budget in range(budget_range["min"], budget_range["max"] + 1, budget_range["step"]):
            plan = main.build_restock_plan(budget)
            lines = plan["items"]
            partial_positions = [i for i, line in enumerate(lines) if line["is_partial"]]
            ranks = [RANKED_SKUS.index(line["sku"]) for line in lines]

            assert lines, f"Empty plan at {budget}"
            assert plan["total_cost"] <= budget
            assert abs(sum(line["line_total"] for line in lines) - plan["total_cost"]) < 0.01
            assert abs(plan["total_cost"] + plan["unspent"] - budget) < 0.01
            assert len(partial_positions) <= 1
            assert partial_positions in ([], [len(lines) - 1])
            assert ranks == sorted(ranks)

    def test_recommendations_skip_unaffordable_item(self, client):
        """Test that an unaffordable top item doesn't stop cheaper purchases."""
        plan = client.get("/api/restocking/recommendations?budget=20").json()

        assert [line["sku"] for line in plan["items"]] == ["GSK-203"]
        assert plan["items"][0]["quantity"] == 3
        assert plan["unspent"] == 0.80

    def test_recommendations_budget_below_cheapest_unit(self, client):
        """Test that a budget below every unit cost gives an empty plan."""
        response = client.get("/api/restocking/recommendations?budget=5")
        assert response.status_code == 200

        plan = response.json()
        assert plan["items"] == []
        assert plan["total_cost"] == 0
        assert plan["lead_time_days"] is None
        assert plan["fully_funded"] is False

    def test_recommendations_zero_budget(self, client):
        """Test that a zero budget gives an empty plan."""
        response = client.get("/api/restocking/recommendations?budget=0")
        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_recommendations_fully_funded(self, client):
        """Test that the slider maximum funds every item in full."""
        plan = client.get("/api/restocking/recommendations?budget=84300").json()

        assert [line["sku"] for line in plan["items"]] == RANKED_SKUS
        assert plan["fully_funded"] is True
        assert plan["unfunded_count"] == 0
        assert plan["total_cost"] == 84285.98
        assert plan["unspent"] == 14.02
        assert plan["lead_time_days"] == 30

    @pytest.mark.parametrize("budget", ["-1", "10000001", "inf", "nan", "abc"])
    def test_recommendations_invalid_budget(self, client, budget):
        """Test that invalid budgets are rejected."""
        response = client.get(f"/api/restocking/recommendations?budget={budget}")
        assert response.status_code == 422

    def test_recommendations_sub_cent_budget(self, monkeypatch):
        """Test that a fraction of a cent is dropped, not rounded up."""
        monkeypatch.setattr(main, "demand_forecasts", [make_forecast("A-1", unit_cost=0.01, forecast=100000)])

        plan = main.build_restock_plan(100.129)

        assert plan["items"][0]["quantity"] == 10012
        assert plan["total_cost"] == 100.12

    def test_recommendations_float_precision(self, monkeypatch):
        """Test a budget whose float times 100 falls below the true value."""
        monkeypatch.setattr(main, "demand_forecasts", [make_forecast("A-1", unit_cost=0.01, forecast=100000)])

        plan = main.build_restock_plan(100.29)

        assert plan["items"][0]["quantity"] == 10029

    def test_ranking_trend_before_growth(self, monkeypatch):
        """Test that trend outranks growth, and unknown trends come last."""
        monkeypatch.setattr(main, "demand_forecasts", [
            make_forecast("D-1", current=100, forecast=90, trend="decreasing"),
            make_forecast("U-1", current=100, forecast=500, trend="unknown"),
            make_forecast("S-1", current=100, forecast=300, trend="stable"),
            make_forecast("I-1", current=100, forecast=110, trend="Increasing"),
        ])

        assert [f["item_sku"] for f in main.rank_forecasts()] == ["I-1", "S-1", "D-1", "U-1"]

    def test_ranking_ties_break_by_sku(self, monkeypatch):
        """Test that equal items have a stable order."""
        monkeypatch.setattr(main, "demand_forecasts", [make_forecast("B-1"), make_forecast("A-1")])

        assert [f["item_sku"] for f in main.rank_forecasts()] == ["A-1", "B-1"]

    def test_zero_current_demand(self, monkeypatch):
        """Test that zero current demand doesn't divide by zero."""
        monkeypatch.setattr(main, "demand_forecasts", [make_forecast("A-1", current=0, forecast=5)])

        plan = main.build_restock_plan(1000)

        assert plan["items"][0]["growth_pct"] == 0

    def test_unpurchasable_forecasts_excluded(self, monkeypatch):
        """Test that zero-cost and zero-demand forecasts are left out."""
        monkeypatch.setattr(main, "demand_forecasts", [
            make_forecast("A-1", unit_cost=0),
            make_forecast("B-1", forecast=0),
            make_forecast("C-1"),
        ])

        plan = main.build_restock_plan(100000)

        assert [line["sku"] for line in plan["items"]] == ["C-1"]
        assert plan["fully_funded"] is True

    def test_budget_range_with_no_forecasts(self, monkeypatch):
        """Test that no forecasts gives a zeroed range and an empty plan."""
        monkeypatch.setattr(main, "demand_forecasts", [])

        plan = main.build_restock_plan(1000)

        assert plan["items"] == []
        assert plan["budget_range"]["max"] == 0


class TestRestockingOrders:
    """Test suite for placing and listing restocking orders."""

    def test_place_order(self, client):
        """Test placing an order."""
        response = place_order(client)
        assert response.status_code == 201

        order = response.json()
        for field in ["id", "order_number", "status", "order_date", "expected_delivery",
                      "lead_time_days", "budget", "total_cost", "items"]:
            assert field in order
        assert "request_id" not in order
        assert order["status"] == "Submitted"
        assert order["total_cost"] == 42058.50
        assert order["lead_time_days"] == 15

    def test_order_matches_preview(self, client):
        """Test that an order contains exactly what the preview showed."""
        plan = client.get("/api/restocking/recommendations?budget=30000").json()
        order = place_order(client, budget=30000).json()

        assert order["items"] == plan["items"]
        assert order["total_cost"] == plan["total_cost"]

    def test_order_numbers_increment(self, client):
        """Test that each order gets the next number."""
        first = place_order(client, request_id="request-0001").json()
        second = place_order(client, request_id="request-0002").json()
        year = datetime.now().year

        assert first["order_number"] == f"RST-{year}-0001"
        assert second["order_number"] == f"RST-{year}-0002"
        assert first["id"] != second["id"]

    def test_get_orders_newest_first(self, client):
        """Test that submitted orders are listed newest first."""
        place_order(client, request_id="request-0001")
        place_order(client, request_id="request-0002")

        response = client.get("/api/restocking/orders")
        assert response.status_code == 200

        numbers = [order["order_number"] for order in response.json()]
        assert numbers[0].endswith("0002")
        assert numbers[1].endswith("0001")

    def test_get_orders_empty(self, client):
        """Test that no orders gives an empty list."""
        response = client.get("/api/restocking/orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_expected_delivery_calculation(self, client):
        """Test that expected delivery is the order date plus the lead time."""
        order = place_order(client).json()

        order_date = datetime.fromisoformat(order["order_date"])
        expected = datetime.fromisoformat(order["expected_delivery"])
        assert (expected - order_date).days == order["lead_time_days"]
        assert "T" in order["expected_delivery"]

    def test_lead_time_ignores_unfunded_items(self, client):
        """Test that the 30-day item doesn't count when it isn't in the order."""
        order = place_order(client, budget=1000).json()

        assert "MTR-304" not in [line["sku"] for line in order["items"]]
        assert order["lead_time_days"] == 14

    def test_lead_time_uses_slowest_item(self, client):
        """Test that the order takes the lead time of its slowest item."""
        order = place_order(client, budget=84300).json()

        assert order["lead_time_days"] == max(line["lead_time_days"] for line in order["items"])
        assert order["lead_time_days"] == 30

    def test_place_order_budget_too_low(self, client):
        """Test that an order that would be empty is rejected."""
        response = place_order(client, budget=5)
        assert response.status_code == 400
        assert "detail" in response.json()
        assert client.get("/api/restocking/orders").json() == []

    @pytest.mark.parametrize("field,value", [
        ("unit_cost", 0.01),
        ("items", [{"sku": "WDG-001", "quantity": 1}]),
        ("lead_time_days", 1),
    ])
    def test_place_order_rejects_client_values(self, client, field, value):
        """Test that the client can't supply prices, items or lead times."""
        response = client.post(
            "/api/restocking/orders",
            json={"budget": 42100, "request_id": "request-0001", field: value},
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("body", [
        {"budget": 0, "request_id": "request-0001"},
        {"budget": -1, "request_id": "request-0001"},
        {"budget": 10000001, "request_id": "request-0001"},
        {"budget": 42100},
        {"budget": 42100, "request_id": "short"},
        {"request_id": "request-0001"},
    ])
    def test_place_order_invalid_body(self, client, body):
        """Test that invalid request bodies are rejected."""
        response = client.post("/api/restocking/orders", json=body)
        assert response.status_code == 422

    def test_repeated_request_id_returns_original(self, client):
        """Test that a retry returns the first order instead of creating another."""
        first = place_order(client)
        second = place_order(client)

        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json() == first.json()
        assert len(client.get("/api/restocking/orders").json()) == 1

    def test_orders_do_not_affect_customer_data(self, client):
        """Test that restocking orders stay out of sales figures."""
        paths = [
            "/api/orders",
            "/api/dashboard/summary",
            "/api/reports/quarterly",
            "/api/reports/monthly-trends",
        ]
        before = [client.get(path).json() for path in paths]

        place_order(client)

        assert [client.get(path).json() for path in paths] == before


class TestRestockingData:
    """Test suite for the forecast data restocking depends on."""

    def test_forecasts_have_cost_and_lead_time(self, client):
        """Test that every forecast can be priced and scheduled."""
        for forecast in client.get("/api/demand").json():
            assert isinstance(forecast["unit_cost"], (int, float))
            assert isinstance(forecast["lead_time_days"], int)
            assert forecast["unit_cost"] > 0
            assert forecast["lead_time_days"] > 0

    def test_forecast_cost_matches_inventory(self, client):
        """Test that a forecast SKU found in inventory has the same cost."""
        inventory = {item["sku"]: item for item in client.get("/api/inventory").json()}
        shared = [f for f in client.get("/api/demand").json() if f["item_sku"] in inventory]

        assert len(shared) > 0
        for forecast in shared:
            assert forecast["unit_cost"] == inventory[forecast["item_sku"]]["unit_cost"]
