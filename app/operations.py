from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from enum import Enum
from math import ceil
from argon2.exceptions import VerificationError
from sqlalchemy import select, update, delete, func
from sqlalchemy.dialects.postgresql import insert
from app import settings
from app.errors import BusinessError
from app.security import password_hasher, issue_tokens, decode_token
from app.tables import users, products, orders, order_items, promo_codes, user_operations, refresh_tokens

ACTIVE_ORDERS = ('CREATED','PAYMENT_PENDING')
TRANSITIONS = {'CREATED':('PAYMENT_PENDING','CANCELED'),'PAYMENT_PENDING':('PAID','CANCELED'),'PAID':('SHIPPED',),'SHIPPED':('COMPLETED',)}
MAX_MONEY = Decimal('9999999999.99')


def value(item):
    return item.value if isinstance(item,Enum) else item


def data(body):
    return {key:value(item) for key,item in body.model_dump().items()}


def money(number):
    return Decimal(str(number)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)


def invalid(field, message):
    raise BusinessError('VALIDATION_ERROR',400,'Некорректные данные запроса',{'fields':[{'field':field,'message':message}]})


def now(db):
    return db.scalar(select(func.clock_timestamp()))


def register(body, db):
    fields = data(body)
    with db.begin():
        user = db.execute(insert(users).values(username=fields['username'],password_hash=password_hasher.hash(body.password.get_secret_value()),role=fields['role']).on_conflict_do_nothing(index_elements=['username']).returning(users.c.id,users.c.username,users.c.role)).mappings().first()
        if user is None:
            raise BusinessError('USERNAME_TAKEN',409,'Имя пользователя уже занято')
        return dict(user)


def login(body, db):
    with db.begin():
        user = db.execute(select(users).where(users.c.username == body.username)).mappings().first()
        try:
            if user is None or not password_hasher.verify(user['password_hash'],body.password.get_secret_value()):
                raise VerificationError()
        except VerificationError:
            raise BusinessError('INVALID_CREDENTIALS',401,'Неверное имя пользователя или пароль')
        return issue_tokens(db,user)


def refresh(body, db):
    claims = decode_token(body.refresh_token,'refresh')
    with db.begin():
        token = db.execute(select(refresh_tokens).where(refresh_tokens.c.jti == claims['jti']).with_for_update()).mappings().first()
        if token is None or token['revoked'] or token['user_id'] != claims['sub'] or token['expires_at'] <= now(db):
            raise BusinessError('REFRESH_TOKEN_INVALID',401,'Refresh token недействителен')
        user = db.execute(select(users).where(users.c.id == token['user_id'])).mappings().one()
        db.execute(update(refresh_tokens).where(refresh_tokens.c.jti == token['jti']).values(revoked=True))
        return issue_tokens(db,user)


def check_seller(actor, owner_id):
    if actor['role'] != 'ADMIN' and owner_id != actor['id']:
        raise BusinessError('ACCESS_DENIED',403,'Можно управлять только своими товарами')


def product_by_id(db, id, lock=False):
    query = select(products).where(products.c.id == id)
    product = db.execute(query.with_for_update() if lock else query).mappings().first()
    if product is None:
        raise BusinessError('PRODUCT_NOT_FOUND',404,'Товар не найден',{'product_id':str(id)})
    return dict(product)


def create_product(body, db, actor):
    fields = data(body)
    fields['seller_id'] = actor['id']
    fields['price'] = money(fields['price'])
    with db.begin():
        return dict(db.execute(insert(products).values(**fields).returning(products)).mappings().one())


def get_product(id, db, actor):
    return product_by_id(db,id)


def list_products(db, actor, page=0, size=20, status=None, category=None):
    clauses = []
    if status is not None:
        clauses.append(products.c.status == value(status))
    if category is not None:
        clauses.append(products.c.category == category)
    query = select(products).where(*clauses).order_by(products.c.created_at,products.c.id).offset(page * size).limit(size)
    return {'items':[dict(row) for row in db.execute(query).mappings()], 'totalElements':db.scalar(select(func.count()).select_from(products).where(*clauses)), 'page':page,'size':size}


def update_product(id, body, db, actor):
    with db.begin():
        original = product_by_id(db,id,lock=True)
        check_seller(actor,original['seller_id'])
        fields = data(body)
        fields['price'] = money(fields['price'])
        return dict(db.execute(update(products).where(products.c.id == id).values(**fields).returning(products)).mappings().one())


def delete_product(id, db, actor):
    with db.begin():
        product = product_by_id(db,id,lock=True)
        check_seller(actor,product['seller_id'])
        db.execute(update(products).where(products.c.id == id).values(status='ARCHIVED'))


def create_promo(body, db, actor):
    fields = data(body)
    for field in ('discount_value','min_order_amount'):
        fields[field] = money(fields[field])
    if fields['valid_from'] >= fields['valid_until']:
        invalid('valid_until','Дата окончания должна быть позже даты начала')
    if fields['discount_type'] == 'PERCENTAGE' and fields['discount_value'] > 100:
        invalid('discount_value','Процент должен быть не выше 100')
    with db.begin():
        promo = db.execute(insert(promo_codes).values(**fields).on_conflict_do_nothing(index_elements=['code']).returning(promo_codes)).mappings().first()
        if promo is None:
            raise BusinessError('PROMO_CODE_EXISTS',409,'Промокод уже существует')
        return dict(promo)


def lock_actor(db, actor):
    db.execute(select(users.c.id).where(users.c.id == actor['id']).with_for_update()).one()


def check_limit(db, actor, operation):
    latest = db.scalar(select(func.max(user_operations.c.created_at)).where(user_operations.c.user_id == actor['id'],user_operations.c.operation_type == operation))
    interval = float(settings.ORDER_INTERVAL_MINUTES * 60)
    if latest is not None:
        remaining = interval - (now(db) - latest).total_seconds()
        if remaining > 0:
            raise BusinessError('ORDER_LIMIT_EXCEEDED',429,'Слишком частая операция',{'retry_after_seconds':ceil(remaining)})


def record_operation(db, actor, operation):
    db.execute(insert(user_operations).values(user_id=actor['id'],operation_type=operation))


def order_by_id(db, id, actor, lock=False):
    query = select(orders).where(orders.c.id == id)
    order = db.execute(query.with_for_update() if lock else query).mappings().first()
    if order is None:
        raise BusinessError('ORDER_NOT_FOUND',404,'Заказ не найден',{'order_id':str(id)})
    if actor['role'] != 'ADMIN' and order['user_id'] != actor['id']:
        raise BusinessError('ORDER_OWNERSHIP_VIOLATION',403,'Заказ принадлежит другому пользователю')
    return dict(order)


def order_view(db, id, actor):
    order = order_by_id(db,id,actor)
    order['items'] = [dict(row) for row in db.execute(select(order_items.c.id,order_items.c.product_id,order_items.c.quantity,order_items.c.price_at_order).where(order_items.c.order_id == id).order_by(order_items.c.product_id)).mappings()]
    return order


def require_state(order, allowed):
    if order['status'] not in allowed:
        raise BusinessError('INVALID_STATE_TRANSITION',409,'Операция недоступна в текущем состоянии',{'status':order['status'],'allowed':list(allowed)})


def reserve(db, items, previous=()):
    old = {item['product_id']:item for item in previous}
    ids = sorted({item.product_id for item in items} | old.keys())
    locked = {row['id']:dict(row) for row in db.execute(select(products).where(products.c.id.in_(ids)).order_by(products.c.id).with_for_update()).mappings()}
    for item in items:
        product = locked.get(item.product_id)
        if product is None:
            raise BusinessError('PRODUCT_NOT_FOUND',404,'Товар не найден',{'product_id':str(item.product_id)})
        if product['status'] != 'ACTIVE':
            raise BusinessError('PRODUCT_INACTIVE',409,'Товар неактивен',{'product_id':str(item.product_id)})
    available = {id:product['stock'] + old.get(id,{}).get('quantity',0) for id,product in locked.items()}
    missing = [{'product_id':str(item.product_id),'requested_quantity':item.quantity,'available_stock':available[item.product_id]} for item in items if available[item.product_id] < item.quantity]
    if missing:
        raise BusinessError('INSUFFICIENT_STOCK',409,'Недостаточно товара на складе',{'products':missing})
    new_items = []
    for item in items:
        available[item.product_id] -= item.quantity
        price = old[item.product_id]['price_at_order'] if item.product_id in old else locked[item.product_id]['price']
        new_items.append({'product_id':item.product_id,'quantity':item.quantity,'price_at_order':price})
    for id in ids:
        db.execute(update(products).where(products.c.id == id).values(stock=available[id]))
    return new_items


def subtotal(items):
    amount = sum((item['price_at_order'] * item['quantity'] for item in items),Decimal('0'))
    if amount > MAX_MONEY:
        invalid('items','Сумма заказа превышает NUMERIC(12,2)')
    return money(amount)


def find_promo(db, clause):
    promo = db.execute(select(promo_codes).where(clause).with_for_update()).mappings().first()
    if promo is None:
        raise BusinessError('PROMO_CODE_INVALID',422,'Промокод не найден')
    return dict(promo)


def validate_promo(db, promo, already_used=False):
    current_time = now(db)
    if not promo['active'] or not (promo['valid_from'] <= current_time <= promo['valid_until']) or promo['current_uses'] - int(already_used) >= promo['max_uses']:
        raise BusinessError('PROMO_CODE_INVALID',422,'Промокод недействителен')


def discount_for(promo, amount):
    if promo['discount_type'] == 'FIXED_AMOUNT':
        return min(promo['discount_value'],amount)
    if promo['discount_value'] > 70:
        return Decimal('0.00')
    cap = (amount * Decimal('0.70')).quantize(Decimal('0.01'),rounding=ROUND_DOWN)
    return min(money(amount * promo['discount_value'] / 100),cap)


def create_order(body, db, actor):
    with db.begin():
        lock_actor(db,actor)
        check_limit(db,actor,'CREATE_ORDER')
        active = db.scalar(select(orders.c.id).where(orders.c.user_id == actor['id'],orders.c.status.in_(ACTIVE_ORDERS)).limit(1))
        if active is not None:
            raise BusinessError('ORDER_HAS_ACTIVE',409,'У пользователя уже есть активный заказ')
        items = reserve(db,body.items)
        amount = subtotal(items)
        discount = Decimal('0.00')
        promo_id = None
        if body.promo_code:
            promo = find_promo(db,promo_codes.c.code == body.promo_code)
            validate_promo(db,promo)
            if amount < promo['min_order_amount']:
                raise BusinessError('PROMO_CODE_MIN_AMOUNT',422,'Сумма заказа ниже минимальной',{'min_order_amount':str(promo['min_order_amount'])})
            discount = discount_for(promo,amount)
            promo_id = promo['id']
            db.execute(update(promo_codes).where(promo_codes.c.id == promo_id).values(current_uses=promo_codes.c.current_uses + 1))
        id = db.scalar(insert(orders).values(user_id=actor['id'],status='CREATED',promo_code_id=promo_id,total_amount=amount-discount,discount_amount=discount).returning(orders.c.id))
        db.execute(insert(order_items),[dict(item,order_id=id) for item in items])
        record_operation(db,actor,'CREATE_ORDER')
        return order_view(db,id,actor)


def get_order(id, db, actor):
    with db.begin():
        order_by_id(db,id,actor,lock=True)
        return order_view(db,id,actor)


def update_order(id, body, db, actor):
    with db.begin():
        lock_actor(db,actor)
        original = order_by_id(db,id,actor,lock=True)
        require_state(original,('CREATED',))
        check_limit(db,actor,'UPDATE_ORDER')
        previous = db.execute(select(order_items).where(order_items.c.order_id == id)).mappings().all()
        items = reserve(db,body.items,previous)
        amount = subtotal(items)
        discount = Decimal('0.00')
        promo_id = original['promo_code_id']
        if promo_id is not None:
            promo = find_promo(db,promo_codes.c.id == promo_id)
            validate_promo(db,promo,already_used=True)
            if amount < promo['min_order_amount']:
                db.execute(update(promo_codes).where(promo_codes.c.id == promo_id).values(current_uses=promo_codes.c.current_uses - 1))
                promo_id = None
            else:
                discount = discount_for(promo,amount)
        db.execute(delete(order_items).where(order_items.c.order_id == id))
        db.execute(insert(order_items),[dict(item,order_id=id) for item in items])
        db.execute(update(orders).where(orders.c.id == id).values(total_amount=amount-discount,discount_amount=discount,promo_code_id=promo_id))
        record_operation(db,actor,'UPDATE_ORDER')
        return order_view(db,id,actor)


def cancel_order(id, db, actor):
    with db.begin():
        lock_actor(db,actor)
        order = order_by_id(db,id,actor,lock=True)
        require_state(order,ACTIVE_ORDERS)
        previous = db.execute(select(order_items).where(order_items.c.order_id == id)).mappings().all()
        ids = [item['product_id'] for item in previous]
        db.execute(select(products.c.id).where(products.c.id.in_(ids)).order_by(products.c.id).with_for_update()).all()
        for item in previous:
            db.execute(update(products).where(products.c.id == item['product_id']).values(stock=products.c.stock + item['quantity']))
        if order['promo_code_id'] is not None:
            promo = find_promo(db,promo_codes.c.id == order['promo_code_id'])
            db.execute(update(promo_codes).where(promo_codes.c.id == promo['id']).values(current_uses=promo_codes.c.current_uses - 1))
        set_order_status(db,order,'CANCELED')
        return order_view(db,id,actor)


def set_order_status(db, order, target):
    if target not in TRANSITIONS.get(order['status'],()):
        raise BusinessError('INVALID_STATE_TRANSITION',409,'Недопустимый переход состояния',{'from':order['status'],'to':target})
    db.execute(update(orders).where(orders.c.id == order['id']).values(status=target))
