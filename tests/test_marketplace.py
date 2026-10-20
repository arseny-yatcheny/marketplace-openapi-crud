import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4
import jwt
import pytest
from sqlalchemy import text
from app import settings
from app.db import engine


def sql(statement, **params):
    with engine.begin() as db:
        result = db.execute(text(statement),params)
        return [dict(row) for row in result.mappings()] if result.returns_rows else None


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()['error_code'] == code, response.text
    UUID(response.headers['x-request-id'])


def create_order(client, actor, product, quantity=1, **extra):
    return client.post('/orders',headers=actor['headers'],json={'items':[{'product_id':product['id'],'quantity':quantity}],**extra})


def update_order(client, actor, order, product, quantity):
    return client.put('/orders/'+order['id'],headers=actor['headers'],json={'items':[{'product_id':product['id'],'quantity':quantity}]})


def cancel(client, actor, order):
    return client.post('/orders/'+order['id']+'/cancel',headers=actor['headers'])


def stock(id):
    return sql('SELECT stock FROM products WHERE id=:id',id=id)[0]['stock']


def test_register_login_refresh(client):
    credentials = {'username':'student','password':'password-123'}
    assert client.post('/auth/register',json=credentials).status_code == 201
    error(client.post('/auth/register',json=credentials),409,'USERNAME_TAKEN')
    tokens = client.post('/auth/login',json=credentials).json()
    assert tokens['expires_in'] == 1200
    error(client.post('/auth/login',json={**credentials,'password':'incorrect-password'}),401,'INVALID_CREDENTIALS')
    refreshed = client.post('/auth/refresh',json={'refresh_token':tokens['refresh_token']})
    assert refreshed.status_code == 200
    assert refreshed.json()['refresh_token'] != tokens['refresh_token']
    error(client.post('/auth/refresh',json={'refresh_token':tokens['refresh_token']}),401,'REFRESH_TOKEN_INVALID')
    error(client.post('/auth/refresh',json={'refresh_token':tokens['access_token']}),401,'REFRESH_TOKEN_INVALID')
    error(client.get('/products',headers={'Authorization':'Bearer '+tokens['refresh_token']}),401,'TOKEN_INVALID')
    error(client.post('/auth/register',json={**credentials,'username':'admin2','role':'ADMIN'}),400,'VALIDATION_ERROR')
    assert sql('SELECT password_hash FROM users')[0]['password_hash'].startswith('$argon2')


def test_tokens(client, account):
    user = account()
    error(client.get('/products'),401,'TOKEN_INVALID')
    error(client.get('/products',headers={'Authorization':'Bearer nonsense'}),401,'TOKEN_INVALID')
    payload = jwt.decode(user['tokens']['access_token'],settings.JWT_SECRET,algorithms=['HS256'],audience='marketplace')
    payload['exp'] = datetime.now(timezone.utc)-timedelta(seconds=1)
    token = jwt.encode(payload,settings.JWT_SECRET,algorithm='HS256')
    error(client.get('/products',headers={'Authorization':'Bearer '+token}),401,'TOKEN_EXPIRED')
    error(client.post('/auth/refresh',json={'refresh_token':token}),401,'REFRESH_TOKEN_INVALID')


def test_product_crud_and_filters(client, actors, product):
    first = product(description=None)
    second = product(category='books',status='INACTIVE')
    result = client.get('/products?page=0&size=1',headers=actors['user']['headers']).json()
    assert result['totalElements'] == 2 and len(result['items']) == 1
    assert result['page'] == 0 and result['size'] == 1
    assert client.get('/products?page=50',headers=actors['user']['headers']).json()['items'] == []
    result = client.get('/products?category=books&status=INACTIVE',headers=actors['user']['headers']).json()
    assert [p['id'] for p in result['items']] == [second['id']]
    assert client.get('/products?category=Books',headers=actors['user']['headers']).json()['totalElements'] == 0
    body = {key:first[key] for key in ('name','price','stock','category','status')}
    body['price'] = 125.25
    updated = client.put('/products/'+first['id'],headers=actors['seller']['headers'],json=body).json()
    assert updated['price'] == 125.25 and updated['created_at'] == first['created_at']
    assert updated['updated_at'] > first['updated_at']
    assert client.delete('/products/'+first['id'],headers=actors['seller']['headers']).status_code == 204
    assert client.delete('/products/'+first['id'],headers=actors['seller']['headers']).status_code == 204
    assert client.get('/products/'+first['id'],headers=actors['user']['headers']).json()['status'] == 'ARCHIVED'
    assert sql('SELECT COUNT(*) AS n FROM products')[0]['n'] == 2
    error(client.get('/products/'+str(uuid4()),headers=actors['user']['headers']),404,'PRODUCT_NOT_FOUND')


@pytest.mark.parametrize('field,bad',[('name',''),('name','x'*256),('description','x'*4001),('price',0),('price',-1),('price',0.001),('price','10'),('stock',-1),('stock',1.1),('stock',True),('category',''),('category','x'*101),('status','UNKNOWN'),('name',None),('stock',None)])
def test_product_validation(client, actors, field, bad):
    body = {'name':'Thing','price':10,'stock':1,'category':'things','status':'ACTIVE',field:bad}
    response = client.post('/products',headers=actors['seller']['headers'],json=body)
    error(response,400,'VALIDATION_ERROR')
    assert response.json()['details']['fields']
    assert sql('SELECT COUNT(*) AS n FROM products')[0]['n'] == 0


@pytest.mark.parametrize('query',['page=-1','size=0','size=101','status=UNKNOWN','category=','page=abc'])
def test_query_validation(client, actors, query):
    error(client.get('/products?'+query,headers=actors['user']['headers']),400,'VALIDATION_ERROR')


def test_product_permissions(client, actors, product):
    item = product()
    body = {key:item[key] for key in ('name','price','stock','category','status')}
    error(client.post('/products',headers=actors['user']['headers'],json=body),403,'ACCESS_DENIED')
    for actor in ('user','other_seller'):
        error(client.put('/products/'+item['id'],headers=actors[actor]['headers'],json=body),403,'ACCESS_DENIED')
        error(client.delete('/products/'+item['id'],headers=actors[actor]['headers']),403,'ACCESS_DENIED')
    error(client.post('/products',headers=actors['seller']['headers'],json={**body,'seller_id':actors['other_seller']['id']}),403,'ACCESS_DENIED')
    assert client.put('/products/'+item['id'],headers=actors['admin']['headers'],json=body).status_code == 200


def test_order_reservation_snapshot_update_and_cancel(client, actors, product, promo):
    item = product()
    promo(max_uses=1)
    response = create_order(client,actors['user'],item,2,promo_code='SALE20')
    assert response.status_code == 201
    order = response.json()
    assert order['total_amount'] == 160 and order['discount_amount'] == 40
    assert stock(item['id']) == 18
    sql('UPDATE products SET price=999 WHERE id=:id',id=item['id'])
    assert client.get('/orders/'+order['id'],headers=actors['user']['headers']).json()['items'][0]['price_at_order'] == 100
    changed = update_order(client,actors['user'],order,item,3).json()
    assert changed['total_amount'] == 240
    assert stock(item['id']) == 17
    assert sql('SELECT current_uses FROM promo_codes')[0]['current_uses'] == 1
    assert cancel(client,actors['user'],order).json()['status'] == 'CANCELED'
    assert stock(item['id']) == 20
    assert sql('SELECT current_uses FROM promo_codes')[0]['current_uses'] == 0
    error(cancel(client,actors['user'],order),409,'INVALID_STATE_TRANSITION')
    assert stock(item['id']) == 20


def test_promo_removal_below_minimum(client, actors, product, promo):
    item = product()
    promo(min_order_amount=200)
    order = create_order(client,actors['user'],item,3,promo_code='SALE20').json()
    updated = update_order(client,actors['user'],order,item,1).json()
    assert updated['promo_code_id'] is None and updated['discount_amount'] == 0
    assert updated['total_amount'] == 100
    assert sql('SELECT current_uses FROM promo_codes')[0]['current_uses'] == 0


def test_order_errors_and_atomic_rollback(client, actors, product):
    first = product(stock=3)
    second = product(stock=0)
    request = {'items':[{'product_id':first['id'],'quantity':2},{'product_id':second['id'],'quantity':1}]}
    response = client.post('/orders',headers=actors['user']['headers'],json=request)
    error(response,409,'INSUFFICIENT_STOCK')
    assert response.json()['details']['products'][0]['product_id'] == second['id']
    assert stock(first['id']) == 3 and stock(second['id']) == 0
    error(create_order(client,actors['user'],first,promo_code='NOPE'),422,'PROMO_CODE_INVALID')
    assert stock(first['id']) == 3
    assert sql('SELECT COUNT(*) AS n FROM user_operations')[0]['n'] == 0
    assert sql('SELECT COUNT(*) AS n FROM orders')[0]['n'] == 0
    order = create_order(client,actors['user'],first).json()
    error(update_order(client,actors['user'],order,second,1),409,'INSUFFICIENT_STOCK')
    assert stock(first['id']) == 2
    assert client.get('/orders/'+order['id'],headers=actors['user']['headers']).json()['items'][0]['product_id'] == first['id']


def test_missing_inactive_and_archived_products(client, actors, product):
    error(create_order(client,actors['user'],{'id':str(uuid4())}),404,'PRODUCT_NOT_FOUND')
    for state in ('INACTIVE','ARCHIVED'):
        item = product(status=state)
        error(create_order(client,actors['user'],item),409,'PRODUCT_INACTIVE')


def test_order_ownership_and_roles(client, actors, product):
    item = product()
    order = create_order(client,actors['user'],item).json()
    error(create_order(client,actors['user'],item),409,'ORDER_HAS_ACTIVE')
    error(create_order(client,actors['seller'],item),403,'ACCESS_DENIED')
    error(client.get('/orders/'+order['id'],headers=actors['seller']['headers']),403,'ACCESS_DENIED')
    error(client.get('/orders/'+order['id'],headers=actors['other']['headers']),403,'ORDER_OWNERSHIP_VIOLATION')
    error(update_order(client,actors['other'],order,item,2),403,'ORDER_OWNERSHIP_VIOLATION')
    error(cancel(client,actors['other'],order),403,'ORDER_OWNERSHIP_VIOLATION')
    assert update_order(client,actors['admin'],order,item,2).status_code == 200
    assert cancel(client,actors['admin'],order).status_code == 200
    error(client.get('/orders/'+str(uuid4()),headers=actors['user']['headers']),404,'ORDER_NOT_FOUND')


@pytest.mark.parametrize('quantity',[0,1000,-1,1.5,True])
def test_order_quantity_validation(client, actors, product, quantity):
    item = product()
    error(create_order(client,actors['user'],item,quantity),400,'VALIDATION_ERROR')
    assert stock(item['id']) == 20


def test_order_input_shape(client, actors, product):
    item = product()
    line = {'product_id':item['id'],'quantity':1}
    for items in (None,[],[line]*51,[line,line],[line,{**line,'product_id':item['id'].upper()}]):
        error(client.post('/orders',headers=actors['user']['headers'],json={'items':items}),400,'VALIDATION_ERROR')
    error(create_order(client,actors['user'],item,promo_code='bad'),400,'VALIDATION_ERROR')
    error(client.get('/orders/bad-uuid',headers=actors['user']['headers']),400,'VALIDATION_ERROR')
    error(client.post('/orders',headers=actors['user']['headers'],json={'items':[line],'unknown':1}),400,'VALIDATION_ERROR')


def test_order_rate_limits_and_precedence(client, actors, product, monkeypatch):
    monkeypatch.setattr(settings,'ORDER_INTERVAL_MINUTES',Decimal('5'))
    item = product()
    order = create_order(client,actors['user'],item).json()
    error(create_order(client,actors['user'],item),429,'ORDER_LIMIT_EXCEEDED')
    assert update_order(client,actors['user'],order,item,2).status_code == 200
    error(update_order(client,actors['user'],order,item,3),429,'ORDER_LIMIT_EXCEEDED')
    assert stock(item['id']) == 18
    sql("UPDATE user_operations SET created_at=clock_timestamp()-interval '6 minutes'")
    error(create_order(client,actors['user'],item),409,'ORDER_HAS_ACTIVE')
    assert update_order(client,actors['user'],order,item,3).status_code == 200


def test_order_lifecycle(client, actors, product):
    item = product()
    order = create_order(client,actors['user'],item).json()
    path = '/orders/'+order['id']+'/status'
    error(client.post(path,headers=actors['admin']['headers'],json={'status':'PAID'}),409,'INVALID_STATE_TRANSITION')
    error(client.post(path,headers=actors['user']['headers'],json={'status':'PAYMENT_PENDING'}),403,'ACCESS_DENIED')
    for state in ('PAYMENT_PENDING','PAID','SHIPPED','COMPLETED'):
        assert client.post(path,headers=actors['admin']['headers'],json={'status':state}).json()['status'] == state
        error(update_order(client,actors['user'],order,item,2),409,'INVALID_STATE_TRANSITION')
        if state != 'PAYMENT_PENDING':
            error(cancel(client,actors['user'],order),409,'INVALID_STATE_TRANSITION')
    assert stock(item['id']) == 19


def test_cancel_pending(client, actors, product):
    item = product()
    order = create_order(client,actors['user'],item).json()
    client.post('/orders/'+order['id']+'/status',headers=actors['admin']['headers'],json={'status':'PAYMENT_PENDING'})
    assert cancel(client,actors['user'],order).status_code == 200
    assert stock(item['id']) == 20


@pytest.mark.parametrize('changes,expected',[({'active':False},'PROMO_CODE_INVALID'),({'valid_until':'2000-01-01T00:00:00Z','valid_from':'1999-01-01T00:00:00Z'},'PROMO_CODE_INVALID'),({'valid_from':'2099-01-01T00:00:00Z','valid_until':'2100-01-01T00:00:00Z'},'PROMO_CODE_INVALID'),({'min_order_amount':200},'PROMO_CODE_MIN_AMOUNT')])
def test_invalid_promos(client, actors, product, promo, changes, expected):
    item = product()
    promo(**changes)
    error(create_order(client,actors['user'],item,promo_code='SALE20'),422,expected)
    assert stock(item['id']) == 20
    assert sql('SELECT current_uses FROM promo_codes')[0]['current_uses'] == 0


@pytest.mark.parametrize('kind,discount,expected',[('PERCENTAGE',70,30),('PERCENTAGE',71,100),('PERCENTAGE',33.33,66.67),('FIXED_AMOUNT',1000,0),('FIXED_AMOUNT',12.34,87.66)])
def test_discount_calculation(client, actors, product, promo, kind, discount, expected):
    item = product()
    promo(discount_type=kind,discount_value=discount)
    response = create_order(client,actors['user'],item,promo_code='SALE20')
    assert response.json()['total_amount'] == expected


def test_invalid_promo_on_update_rolls_back(client, actors, product, promo):
    item = product()
    promo()
    order = create_order(client,actors['user'],item,promo_code='SALE20').json()
    sql('UPDATE promo_codes SET active=false')
    error(update_order(client,actors['user'],order,item,2),422,'PROMO_CODE_INVALID')
    assert stock(item['id']) == 19
    assert sql('SELECT current_uses FROM promo_codes')[0]['current_uses'] == 1
    assert cancel(client,actors['user'],order).status_code == 200


def test_concurrent_stock(client, actors, product):
    item = product(stock=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda actor:create_order(client,actor,item),[actors['user'],actors['other']]))
    assert sorted(response.status_code for response in responses) == [201,409]
    assert stock(item['id']) == 0
    assert sql('SELECT COUNT(*) AS n FROM orders')[0]['n'] == 1


def test_concurrent_active_order(client, actors, product):
    item = product()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _:create_order(client,actors['user'],item),range(2)))
    assert sorted(response.status_code for response in responses) == [201,409]
    assert stock(item['id']) == 19


def test_concurrent_promo_limit(client, actors, product, promo):
    item = product()
    promo(max_uses=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda actor:create_order(client,actor,item,promo_code='SALE20'),[actors['user'],actors['other']]))
    assert sorted(response.status_code for response in responses) == [201,422]
    assert stock(item['id']) == 19
    assert sql('SELECT current_uses FROM promo_codes')[0]['current_uses'] == 1


def test_concurrent_cancel(client, actors, product, promo):
    item = product()
    promo()
    order = create_order(client,actors['user'],item,promo_code='SALE20').json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _:cancel(client,actors['user'],order),range(2)))
    assert sorted(response.status_code for response in responses) == [200,409]
    assert stock(item['id']) == 20
    assert sql('SELECT current_uses FROM promo_codes')[0]['current_uses'] == 0


def test_logging_redaction(client, caplog):
    caplog.set_level(logging.INFO,logger='marketplace.requests')
    response = client.post('/auth/register',json={'username':'logging-user','password':'private-password'})
    records = [json.loads(record.message) for record in caplog.records if record.name == 'marketplace.requests']
    row = records[-1]
    assert row['request_id'] == response.headers['x-request-id']
    assert row['request_body']['password'] == '***'
    assert 'private-password' not in json.dumps(records)
    assert row['user_id'] is None and row['duration_ms'] >= 0
    assert row['status_code'] == 201 and row['method'] == 'POST'
    datetime.fromisoformat(row['timestamp'])


def test_json_and_not_found(client, actors):
    response = client.post('/products',headers={**actors['seller']['headers'],'Content-Type':'application/json'},content='{"name":')
    error(response,400,'VALIDATION_ERROR')
    error(client.get('/missing'),404,'NOT_FOUND')
    assert client.get('/health').json() == {'status':'ok'}
