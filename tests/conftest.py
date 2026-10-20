import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, text
from sqlalchemy.orm import Session
from jsonschema import Draft202012Validator, FormatChecker
from app.db import engine
from app.main import app
from app.security import password_hasher
from app.tables import users
from app.validation import CONTRACT, json_schema


def check_response(response):
    path = response.request.url.path
    method = response.request.method.lower()
    match = next((item for template,item in CONTRACT['paths'].items() if re.fullmatch(re.sub(r'\{[^}]+\}',r'[^/]+',template),path)),None)
    if match is None or method not in match:
        return
    definitions = match[method]['responses']
    assert str(response.status_code) in definitions, response.text
    definition = definitions[str(response.status_code)]
    if 'content' not in definition:
        assert response.content == b''
        return
    schema = dict(definition['content']['application/json']['schema'],components=CONTRACT['components'])
    Draft202012Validator(json_schema(schema),format_checker=FormatChecker()).validate(json.loads(response.text,parse_float=Decimal))


class CheckedClient(TestClient):
    def request(self, *args, **kwargs):
        response = super().request(*args,**kwargs)
        check_response(response)
        return response


@pytest.fixture(autouse=True)
def clean_database():
    assert engine.url.database.endswith('_test'), 'Tests require a dedicated database ending in _test'
    with engine.begin() as connection:
        connection.execute(text('TRUNCATE refresh_tokens, user_operations, order_items, orders, promo_codes, products, users CASCADE'))
    yield


@pytest.fixture
def client():
    with CheckedClient(app) as instance:
        yield instance


@pytest.fixture(scope='session')
def password_hash():
    return password_hasher.hash('test-password-123')


@pytest.fixture
def account(client, password_hash):
    def create(role='USER'):
        id = uuid4()
        username = 'u' + id.hex
        with Session(engine) as db, db.begin():
            db.execute(insert(users).values(id=id,username=username,password_hash=password_hash,role=role))
        response = client.post('/auth/login',json={'username':username,'password':'test-password-123'})
        assert response.status_code == 200, response.text
        tokens = response.json()
        return {'id':str(id),'username':username,'tokens':tokens,'headers':{'Authorization':'Bearer '+tokens['access_token']}}
    return create


@pytest.fixture
def actors(account):
    return {'user':account(),'other':account(),'seller':account('SELLER'),'other_seller':account('SELLER'),'admin':account('ADMIN')}


@pytest.fixture
def product(client, actors):
    def create(**changes):
        fields = {'name':'Keyboard','price':100,'stock':20,'category':'electronics','status':'ACTIVE'}
        fields.update(changes)
        response = client.post('/products',headers=actors['seller']['headers'],json=fields)
        assert response.status_code == 201, response.text
        return response.json()
    return create


@pytest.fixture
def promo(client, actors):
    def create(**changes):
        now = datetime.now(timezone.utc)
        fields = {'code':'SALE20','discount_type':'PERCENTAGE','discount_value':20,'min_order_amount':100,'max_uses':10,'valid_from':(now-timedelta(days=1)).isoformat(),'valid_until':(now+timedelta(days=1)).isoformat(),'active':True}
        fields.update(changes)
        response = client.post('/promo-codes',headers=actors['seller']['headers'],json=fields)
        assert response.status_code == 201, response.text
        return response.json()
    return create
