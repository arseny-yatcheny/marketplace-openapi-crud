import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from uuid import UUID
import yaml
from fastapi import Request
from jsonschema import Draft202012Validator, FormatChecker
from app.errors import BusinessError

CONTRACT = yaml.safe_load((Path(__file__).resolve().parents[1] / 'openapi/marketplace.yaml').read_text())


def json_schema(value):
    if isinstance(value, list):
        return [json_schema(item) for item in value]
    if isinstance(value, float):
        return Decimal(str(value))
    if not isinstance(value, dict):
        return value
    result = {key:json_schema(item) for key,item in value.items() if key != 'nullable'}
    if value.get('nullable'):
        return {'anyOf':[result, {'type':'null'}]}
    return result


validators = {}
for path, methods in CONTRACT['paths'].items():
    for method, operation in methods.items():
        if 'requestBody' in operation:
            schema = deepcopy(operation['requestBody']['content']['application/json']['schema'])
            schema['components'] = CONTRACT['components']
            validators[(path,method)] = Draft202012Validator(json_schema(schema), format_checker=FormatChecker())


async def validate_contract(request: Request):
    path = request.scope['route'].path
    validator = validators.get((path,request.method.lower()))
    if validator is None:
        return
    try:
        body = json.loads(await request.body(), parse_float=Decimal, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeDecodeError):
        raise BusinessError('VALIDATION_ERROR',400,'Некорректный JSON',{'fields':[{'field':'body','message':'Ожидается JSON'}]})
    fields = [{'field':'body.' + '.'.join(str(p) for p in error.path), 'message':error.message} for error in validator.iter_errors(body)]
    if path in ('/orders','/orders/{id}') and request.method in ('POST','PUT') and isinstance(body,dict) and isinstance(body.get('items'),list):
        seen = set()
        for index,item in enumerate(body.get('items', [])):
            if not isinstance(item,dict):
                continue
            product_id = item.get('product_id')
            try:
                product_id = UUID(product_id)
            except (ValueError,TypeError,AttributeError):
                continue
            if product_id in seen:
                fields.append({'field':f'body.items.{index}.product_id','message':'product_id не должен повторяться'})
            seen.add(product_id)
    if fields:
        raise BusinessError('VALIDATION_ERROR',400,'Некорректные данные запроса',{'fields':fields})
