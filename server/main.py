import threading
from datetime import datetime, timedelta
from decimal import Decimal
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field
from mock_data import inventory_items, orders, demand_forecasts, backlog_items, spending_summary, monthly_spending, category_spending, recent_transactions, purchase_orders

app = FastAPI(title="Factory Inventory Management System")

# Quarter mapping for date filtering
QUARTER_MAP = {
    'Q1-2025': ['2025-01', '2025-02', '2025-03'],
    'Q2-2025': ['2025-04', '2025-05', '2025-06'],
    'Q3-2025': ['2025-07', '2025-08', '2025-09'],
    'Q4-2025': ['2025-10', '2025-11', '2025-12']
}

# Restocking settings
RESTOCK_BUDGET_STEP = 100
RESTOCK_MAX_BUDGET = 10_000_000
RESTOCK_TREND_RANK = {'increasing': 0, 'stable': 1, 'decreasing': 2}

# Restocking orders are purchases, not customer sales. Keeping them out of `orders`
# stops them from counting towards revenue, the dashboard and reports.
# Held in memory only, so they are cleared when the server restarts.
submitted_orders: list = []

# Routes run in a threadpool. Holding this across the request_id check, numbering and
# append stops concurrent requests creating duplicate orders or order numbers.
submitted_orders_lock = threading.Lock()

def filter_by_month(items: list, month: Optional[str]) -> list:
    """Filter items by month/quarter based on order_date field"""
    if not month or month == 'all':
        return items

    if month.startswith('Q'):
        # Handle quarters
        if month in QUARTER_MAP:
            months = QUARTER_MAP[month]
            return [item for item in items if any(m in item.get('order_date', '') for m in months)]
    else:
        # Direct month match
        return [item for item in items if month in item.get('order_date', '')]

    return items

def apply_filters(items: list, warehouse: Optional[str] = None, category: Optional[str] = None,
                 status: Optional[str] = None) -> list:
    """Apply common filters to a list of items"""
    filtered = items

    if warehouse and warehouse != 'all':
        filtered = [item for item in filtered if item.get('warehouse') == warehouse]

    if category and category != 'all':
        filtered = [item for item in filtered if item.get('category', '').lower() == category.lower()]

    if status and status != 'all':
        filtered = [item for item in filtered if item.get('status', '').lower() == status.lower()]

    return filtered

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class InventoryItem(BaseModel):
    id: str
    sku: str
    name: str
    category: str
    warehouse: str
    quantity_on_hand: int
    reorder_point: int
    unit_cost: float
    location: str
    last_updated: str

class Order(BaseModel):
    id: str
    order_number: str
    customer: str
    items: List[dict]
    status: str
    order_date: str
    expected_delivery: str
    total_value: float
    actual_delivery: Optional[str] = None
    warehouse: Optional[str] = None
    category: Optional[str] = None

class DemandForecast(BaseModel):
    id: str
    item_sku: str
    item_name: str
    current_demand: int
    forecasted_demand: int
    trend: str
    period: str
    unit_cost: float
    lead_time_days: int

class BacklogItem(BaseModel):
    id: str
    order_id: str
    item_sku: str
    item_name: str
    quantity_needed: int
    quantity_available: int
    days_delayed: int
    priority: str
    has_purchase_order: Optional[bool] = False

class PurchaseOrder(BaseModel):
    id: str
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    status: str
    created_date: str
    notes: Optional[str] = None

class CreatePurchaseOrderRequest(BaseModel):
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    notes: Optional[str] = None

class RestockLine(BaseModel):
    sku: str
    name: str
    trend: str
    growth_pct: float
    target_quantity: int
    quantity: int
    unit_cost: float
    line_total: float
    lead_time_days: int
    is_partial: bool

class RestockBudgetRange(BaseModel):
    min: int
    max: int
    step: int
    default: int

class RestockPlan(BaseModel):
    budget: float
    items: List[RestockLine]
    total_cost: float
    unspent: float
    lead_time_days: Optional[int] = None
    fully_funded: bool
    unfunded_count: int
    budget_range: RestockBudgetRange

class CreateRestockOrderRequest(BaseModel):
    # Forbidding extra fields rejects client-supplied prices or items outright.
    # The order is always rebuilt from the budget and the server's forecast data.
    model_config = ConfigDict(extra="forbid")

    budget: float = Field(gt=0, le=RESTOCK_MAX_BUDGET, allow_inf_nan=False)
    request_id: str = Field(min_length=8, max_length=64)

class RestockOrder(BaseModel):
    id: str
    order_number: str
    status: str
    order_date: str
    expected_delivery: str
    lead_time_days: int
    budget: float
    total_cost: float
    items: List[RestockLine]

# API endpoints
@app.get("/")
def root():
    return {"message": "Factory Inventory Management System API", "version": "1.0.0"}

@app.get("/api/inventory", response_model=List[InventoryItem])
def get_inventory(
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Get all inventory items with optional filtering"""
    return apply_filters(inventory_items, warehouse, category)

@app.get("/api/inventory/{item_id}", response_model=InventoryItem)
def get_inventory_item(item_id: str):
    """Get a specific inventory item"""
    item = next((item for item in inventory_items if item["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@app.get("/api/orders", response_model=List[Order])
def get_orders(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get all orders with optional filtering"""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)
    return filtered_orders

@app.get("/api/orders/{order_id}", response_model=Order)
def get_order(order_id: str):
    """Get a specific order"""
    order = next((order for order in orders if order["id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

@app.get("/api/demand", response_model=List[DemandForecast])
def get_demand_forecasts():
    """Get demand forecasts"""
    return demand_forecasts

@app.get("/api/backlog", response_model=List[BacklogItem])
def get_backlog():
    """Get backlog items with purchase order status"""
    # Add has_purchase_order flag to each backlog item
    result = []
    for item in backlog_items:
        item_dict = dict(item)
        # Check if this backlog item has a purchase order
        has_po = any(po["backlog_item_id"] == item["id"] for po in purchase_orders)
        item_dict["has_purchase_order"] = has_po
        result.append(item_dict)
    return result

@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get summary statistics for dashboard with optional filtering"""
    # Filter inventory
    filtered_inventory = apply_filters(inventory_items, warehouse, category)

    # Filter orders
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    total_inventory_value = sum(item["quantity_on_hand"] * item["unit_cost"] for item in filtered_inventory)
    low_stock_items = len([item for item in filtered_inventory if item["quantity_on_hand"] <= item["reorder_point"]])
    pending_orders = len([order for order in filtered_orders if order["status"] in ["Processing", "Backordered"]])
    total_backlog_items = len(backlog_items)

    return {
        "total_inventory_value": round(total_inventory_value, 2),
        "low_stock_items": low_stock_items,
        "pending_orders": pending_orders,
        "total_backlog_items": total_backlog_items,
        "total_orders_value": sum(order["total_value"] for order in filtered_orders)
    }

@app.get("/api/spending/summary")
def get_spending_summary():
    """Get spending summary statistics"""
    return spending_summary

@app.get("/api/spending/monthly")
def get_monthly_spending():
    """Get monthly spending breakdown"""
    return monthly_spending

@app.get("/api/spending/categories")
def get_category_spending():
    """Get spending by category"""
    return category_spending

@app.get("/api/spending/transactions")
def get_recent_transactions():
    """Get recent transactions"""
    return recent_transactions

@app.get("/api/reports/quarterly")
def get_quarterly_reports():
    """Get quarterly performance reports"""
    # Calculate quarterly statistics from orders
    quarters = {}

    for order in orders:
        order_date = order.get('order_date', '')
        # Determine quarter
        if '2025-01' in order_date or '2025-02' in order_date or '2025-03' in order_date:
            quarter = 'Q1-2025'
        elif '2025-04' in order_date or '2025-05' in order_date or '2025-06' in order_date:
            quarter = 'Q2-2025'
        elif '2025-07' in order_date or '2025-08' in order_date or '2025-09' in order_date:
            quarter = 'Q3-2025'
        elif '2025-10' in order_date or '2025-11' in order_date or '2025-12' in order_date:
            quarter = 'Q4-2025'
        else:
            continue

        if quarter not in quarters:
            quarters[quarter] = {
                'quarter': quarter,
                'total_orders': 0,
                'total_revenue': 0,
                'delivered_orders': 0,
                'avg_order_value': 0
            }

        quarters[quarter]['total_orders'] += 1
        quarters[quarter]['total_revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            quarters[quarter]['delivered_orders'] += 1

    # Calculate averages and fulfillment rate
    result = []
    for q, data in quarters.items():
        if data['total_orders'] > 0:
            data['avg_order_value'] = round(data['total_revenue'] / data['total_orders'], 2)
            data['fulfillment_rate'] = round((data['delivered_orders'] / data['total_orders']) * 100, 1)
        result.append(data)

    # Sort by quarter
    result.sort(key=lambda x: x['quarter'])
    return result

@app.get("/api/reports/monthly-trends")
def get_monthly_trends():
    """Get month-over-month trends"""
    months = {}

    for order in orders:
        order_date = order.get('order_date', '')
        if not order_date:
            continue

        # Extract month (format: YYYY-MM-DD)
        month = order_date[:7]  # Gets YYYY-MM

        if month not in months:
            months[month] = {
                'month': month,
                'order_count': 0,
                'revenue': 0,
                'delivered_count': 0
            }

        months[month]['order_count'] += 1
        months[month]['revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            months[month]['delivered_count'] += 1

    # Convert to list and sort
    result = list(months.values())
    result.sort(key=lambda x: x['month'])
    return result

def to_cents(amount: float) -> int:
    """Convert dollars to whole cents, dropping any fraction of a cent"""
    # Going through str() avoids binary float error (100.29 * 100 is 10028.999...).
    # Truncating rather than rounding means a plan can never exceed the budget.
    return int(Decimal(str(amount)) * 100)

def get_growth_pct(forecast: dict) -> float:
    """Get forecast demand growth as a percentage of current demand"""
    current = forecast['current_demand']
    if current <= 0:
        return 0.0
    return (forecast['forecasted_demand'] - current) / current * 100

def rank_forecasts() -> list:
    """Get purchasable forecasts in restocking priority order"""
    purchasable = [f for f in demand_forecasts if f['unit_cost'] > 0 and f['forecasted_demand'] > 0]
    # Growth is ranked by percentage because absolute growth favours high-volume items:
    # +150 units is a 50% rise for WDG-001 but under 19% for FLT-405.
    return sorted(purchasable, key=lambda f: (
        RESTOCK_TREND_RANK.get(f['trend'].lower(), len(RESTOCK_TREND_RANK)),
        -get_growth_pct(f),
        f['item_sku']
    ))

def get_restock_budget_range() -> dict:
    """Derive the budget slider range from forecast costs"""
    forecasts = rank_forecasts()
    if not forecasts:
        return {'min': 0, 'max': 0, 'step': RESTOCK_BUDGET_STEP, 'default': 0}

    step = RESTOCK_BUDGET_STEP * 100
    costs = [to_cents(f['unit_cost']) for f in forecasts]
    full_cost = sum(cost * f['forecasted_demand'] for cost, f in zip(costs, forecasts))

    # Rounding up keeps the minimum at or above the cheapest unit, so every slider
    # position buys something, and the maximum funds every item in full.
    min_steps = -(-min(costs) // step)
    max_steps = -(-full_cost // step)
    return {
        'min': min_steps * RESTOCK_BUDGET_STEP,
        'max': max_steps * RESTOCK_BUDGET_STEP,
        'step': RESTOCK_BUDGET_STEP,
        'default': max(min_steps, max_steps // 2) * RESTOCK_BUDGET_STEP
    }

def build_restock_plan(budget: float) -> dict:
    """Recommend what to restock within a budget.

    The preview and the placed order both use this, so they cannot disagree.
    """
    forecasts = rank_forecasts()
    remaining = to_cents(budget)
    lines = []

    # Money is handled in integer cents so that repeated subtraction cannot drift.
    for forecast in forecasts:
        cost = to_cents(forecast['unit_cost'])
        # The target is one forecast period of cover. The shortfall against current
        # demand would not work: stable items would get 1 or 2 units, and items with
        # falling demand a negative quantity.
        target = forecast['forecasted_demand']
        quantity = min(target, remaining // cost)

        # An item that is too expensive is skipped rather than ending the plan,
        # so the remaining budget can still buy cheaper, lower-ranked items.
        if quantity == 0:
            continue

        remaining -= quantity * cost
        lines.append({
            'sku': forecast['item_sku'],
            'name': forecast['item_name'],
            'trend': forecast['trend'],
            'growth_pct': round(get_growth_pct(forecast), 2),
            'target_quantity': target,
            'quantity': quantity,
            'unit_cost': forecast['unit_cost'],
            'line_total': quantity * cost / 100,
            'lead_time_days': forecast['lead_time_days'],
            'is_partial': quantity < target
        })

        # Stop at the first partial line so that it is always the last line. The leftover
        # is under one unit of this item and is not spent on lower-ranked items.
        if quantity < target:
            break

    # A partial line still counts as unfunded, because the item isn't fully funded.
    unfunded_count = len(forecasts) - sum(1 for line in lines if not line['is_partial'])
    return {
        'budget': budget,
        'items': lines,
        'total_cost': (to_cents(budget) - remaining) / 100,
        'unspent': remaining / 100,
        # The slowest item sets the delivery time. Unfunded items are not in the
        # order, so they do not count.
        'lead_time_days': max((line['lead_time_days'] for line in lines), default=None),
        'fully_funded': bool(lines) and unfunded_count == 0,
        'unfunded_count': unfunded_count,
        'budget_range': get_restock_budget_range()
    }

@app.get("/api/restocking/recommendations", response_model=RestockPlan)
def get_restock_recommendations(
    budget: Optional[float] = Query(None, ge=0, le=RESTOCK_MAX_BUDGET, allow_inf_nan=False)
):
    """Get restocking recommendations for a budget"""
    if budget is None:
        budget = get_restock_budget_range()['default']
    return build_restock_plan(budget)

@app.get("/api/restocking/orders", response_model=List[RestockOrder])
def get_restock_orders():
    """Get submitted restocking orders, newest first"""
    return list(reversed(submitted_orders))

@app.post("/api/restocking/orders", response_model=RestockOrder, status_code=201)
def create_restock_order(request: CreateRestockOrderRequest, response: Response):
    """Place a restocking order for a budget"""
    with submitted_orders_lock:
        # A repeated request_id is a retry of an order that already exists, so
        # return that order rather than buying everything twice.
        for order in submitted_orders:
            if order['request_id'] == request.request_id:
                response.status_code = 200
                return order

        plan = build_restock_plan(request.budget)
        if not plan['items']:
            raise HTTPException(status_code=400, detail="Budget is too low to buy any items")

        now = datetime.now().replace(microsecond=0)
        sequence = len(submitted_orders) + 1
        order = {
            'id': f"rst-{sequence}",
            'order_number': f"RST-{now.year}-{sequence:04d}",
            'request_id': request.request_id,
            'status': 'Submitted',
            'order_date': now.isoformat(),
            # Keeps the time component: a date-only string is parsed as UTC by browsers
            # and shows as the previous day in time zones behind UTC.
            'expected_delivery': (now + timedelta(days=plan['lead_time_days'])).isoformat(),
            'lead_time_days': plan['lead_time_days'],
            'budget': plan['budget'],
            'total_cost': plan['total_cost'],
            'items': plan['items']
        }
        submitted_orders.append(order)
        return order

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
