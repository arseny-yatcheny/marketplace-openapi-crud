from sqlalchemy import Table, Column, MetaData, String, Integer, Numeric, Boolean, DateTime, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID

metadata = MetaData()


def identity():
    return Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def timestamps():
    return [Column("created_at", DateTime(timezone=True), server_default=text("now()")), Column("updated_at", DateTime(timezone=True), server_default=text("now()"))]


users = Table("users", metadata, identity(), Column("username", String(100)), Column("password_hash", String), Column("role", String(20)), *timestamps())
products = Table("products", metadata, identity(), Column("name", String(255)), Column("description", String(4000)), Column("price", Numeric(12, 2)), Column("stock", Integer), Column("category", String(100)), Column("status", String(20)), Column("seller_id", UUID(as_uuid=True), ForeignKey("users.id")), *timestamps())
promo_codes = Table("promo_codes", metadata, identity(), Column("code", String(20)), Column("discount_type", String(20)), Column("discount_value", Numeric(12, 2)), Column("min_order_amount", Numeric(12, 2)), Column("max_uses", Integer), Column("current_uses", Integer, server_default=text("0")), Column("valid_from", DateTime(timezone=True)), Column("valid_until", DateTime(timezone=True)), Column("active", Boolean, server_default=text("true")))
orders = Table("orders", metadata, identity(), Column("user_id", UUID(as_uuid=True), ForeignKey("users.id")), Column("status", String(20)), Column("promo_code_id", UUID(as_uuid=True), ForeignKey("promo_codes.id")), Column("total_amount", Numeric(12, 2)), Column("discount_amount", Numeric(12, 2)), *timestamps())
order_items = Table("order_items", metadata, identity(), Column("order_id", UUID(as_uuid=True), ForeignKey("orders.id")), Column("product_id", UUID(as_uuid=True), ForeignKey("products.id")), Column("quantity", Integer), Column("price_at_order", Numeric(12, 2)))
user_operations = Table("user_operations", metadata, identity(), Column("user_id", UUID(as_uuid=True), ForeignKey("users.id")), Column("operation_type", String(20)), Column("created_at", DateTime(timezone=True), server_default=text("now()")))
refresh_tokens = Table("refresh_tokens", metadata, Column("jti", UUID(as_uuid=True), primary_key=True), Column("user_id", UUID(as_uuid=True), ForeignKey("users.id")), Column("expires_at", DateTime(timezone=True)), Column("revoked", Boolean, server_default=text("false")))
