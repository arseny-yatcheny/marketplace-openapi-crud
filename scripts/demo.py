import json
import os
import secrets
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

BASE_URL = os.getenv('BASE_URL','http://localhost:8000')
suffix = secrets.token_hex(4)
password = secrets.token_urlsafe(16)


def request(method, path, body=None, token=None, expected=200, show=True):
    headers = {'Content-Type':'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE_URL+path,data=data,headers=headers,method=method)
    try:
        response = urllib.request.urlopen(req)
    except urllib.error.HTTPError as exception:
        response = exception
    content = response.read()
    result = json.loads(content) if content else None
    assert response.status == expected, (method,path,response.status,result)
    print(f'{method} {path} → {response.status}; request_id={response.headers.get("X-Request-Id")}')
    if show and result is not None:
        print(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def account(role):
    credentials = {'username':role.lower()+'_'+suffix,'password':password}
    request('POST','/auth/register',{**credentials,'role':role},expected=201)
    return request('POST','/auth/login',credentials,show=False)['access_token']


seller = account('SELLER')
user = account('USER')
item = request('POST','/products',{'name':'Клавиатура','description':'Учебный товар','price':1000,'stock':10,'category':'electronics','status':'ACTIVE'},seller,201)
now = datetime.now(timezone.utc)
code = 'SALE_'+suffix.upper()
request('POST','/promo-codes',{'code':code,'discount_type':'PERCENTAGE','discount_value':20,'min_order_amount':1500,'max_uses':5,'valid_from':(now-timedelta(days=1)).isoformat(),'valid_until':(now+timedelta(days=1)).isoformat()},seller,201)
order = request('POST','/orders',{'items':[{'product_id':item['id'],'quantity':2}],'promo_code':code},user,201)
assert order['total_amount'] == 1600 and order['discount_amount'] == 400
request('GET','/products/'+item['id'],token=user)
request('PUT','/orders/'+order['id'],{'items':[{'product_id':item['id'],'quantity':1}]},user)
request('POST','/orders/'+order['id']+'/cancel',token=user)
request('POST','/orders/'+order['id']+'/cancel',token=user,expected=409)
request('POST','/products',{'name':'','price':0,'stock':-1,'category':'','status':'ACTIVE'},seller,expected=400)
request('GET','/products?status=ACTIVE&category=electronics&page=0&size=20',token=user)
print('\nПроверь в БД:')
print(f"SELECT id, stock, status FROM products WHERE id = '{item['id']}';")
print(f"SELECT id, status, total_amount, discount_amount, promo_code_id FROM orders WHERE id = '{order['id']}';")
print(f"SELECT product_id, quantity, price_at_order FROM order_items WHERE order_id = '{order['id']}';")
print(f"SELECT code, current_uses FROM promo_codes WHERE code = '{code}';")
print("SELECT user_id, operation_type, created_at FROM user_operations ORDER BY created_at DESC LIMIT 10;")
