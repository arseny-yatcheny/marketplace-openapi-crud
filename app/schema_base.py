from decimal import Decimal
from pydantic import BaseModel, ConfigDict, field_serializer


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_value(self, value):
        if isinstance(value, Decimal):
            return float(value)
        return value
