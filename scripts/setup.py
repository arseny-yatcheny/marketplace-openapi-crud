from pathlib import Path
import secrets

destination = Path(__file__).resolve().parents[1] / ".env"
if not destination.exists():
    destination.write_text(f"JWT_SECRET={secrets.token_hex(32)}\nPOSTGRES_PASSWORD={secrets.token_hex(16)}\nORDER_INTERVAL_MINUTES=1\nAPI_PORT=8000\n")
print(".env ready")
