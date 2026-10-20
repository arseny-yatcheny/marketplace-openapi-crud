import os
from decimal import Decimal

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://marketplace:local-marketplace@localhost:5432/marketplace")
JWT_SECRET = os.environ["JWT_SECRET"]
if len(JWT_SECRET) < 32:
    raise RuntimeError("JWT_SECRET must contain at least 32 characters")
ACCESS_MINUTES = 20
REFRESH_DAYS = 14
ORDER_INTERVAL_MINUTES = Decimal(os.getenv("ORDER_INTERVAL_MINUTES", "1"))
if ORDER_INTERVAL_MINUTES < 0:
    raise RuntimeError("ORDER_INTERVAL_MINUTES must be non-negative")
