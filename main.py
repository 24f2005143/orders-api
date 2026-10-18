
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from threading import Lock
from time import monotonic
from math import ceil
from uuid import uuid4

app = FastAPI(title="Orders API")

TOTAL_ORDERS = 52
RATE_LIMIT = 19
WINDOW_SECONDS = 10

lock = Lock()
idempotency_store = {}
rate_buckets = {}

# Fixed catalog with IDs from 1 through 52.
catalog = [
    {"id": i, "product": f"Product {i}", "quantity": 1}
    for i in range(1, TOTAL_ORDERS + 1)
]


class OrderInput(BaseModel):
    product: str = "New Product"
    quantity: int = 1


# Rate-limiting middleware.
# OPTIONS requests must not consume the client's rate limit.
@app.middleware("http")
async def apply_rate_limit(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path == "/health":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "anonymous")
    now = monotonic()

    with lock:
        timestamps = rate_buckets.setdefault(client_id, [])

        # Keep only requests from the last 10 seconds.
        timestamps[:] = [
            timestamp
            for timestamp in timestamps
            if now - timestamp < WINDOW_SECONDS
        ]

        if len(timestamps) >= RATE_LIMIT:
            retry_after = max(
                1,
                ceil(WINDOW_SECONDS - (now - timestamps[0]))
            )

            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )

        timestamps.append(now)

    return await call_next(request)


# Register CORS AFTER the rate-limit middleware.
# Starlette places the last registered middleware on the outside,
# allowing CORS headers to be added to rate-limit error responses too.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {
        "message": "Orders API is running",
        "total_orders": TOTAL_ORDERS,
        "rate_limit": RATE_LIMIT,
        "window_seconds": WINDOW_SECONDS,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/orders", status_code=201)
def create_order(
    data: OrderInput,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    with lock:
        # Return the original order when the key has already been used.
        if idempotency_key in idempotency_store:
            return idempotency_store[idempotency_key]

        order = {
            "id": str(uuid4()),
            "product": data.product,
            "quantity": data.quantity,
        }

        idempotency_store[idempotency_key] = order
        return order


@app.get("/orders")
def get_orders(
    limit: int = Query(default=10, ge=1, le=52),
    cursor: str | None = None,
):
    # Cursor represents the last ID returned.
    if cursor is None or cursor == "":
        start_id = 1
    else:
        try:
            start_id = int(cursor) + 1
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail="Invalid cursor",
            )

    if start_id < 1 or start_id > TOTAL_ORDERS + 1:
        raise HTTPException(
            status_code=400,
            detail="Invalid cursor",
        )

    items = [
        item for item in catalog
        if item["id"] >= start_id
    ][:limit]

    last_id = items[-1]["id"] if items else TOTAL_ORDERS
    next_cursor = str(last_id) if last_id < TOTAL_ORDERS else None

    return {
        "items": items,
        "next_cursor": next_cursor,
    }
