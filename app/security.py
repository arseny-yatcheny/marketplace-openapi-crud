from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
import jwt
from argon2 import PasswordHasher
from fastapi import Request
from fastapi.security import HTTPBearer
from sqlalchemy import insert
from app import settings
from app.errors import BusinessError
from app.tables import refresh_tokens

password_hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)


def decode_token(token, kind):
    try:
        claims = jwt.decode(token, settings.JWT_SECRET, algorithms=['HS256'], audience='marketplace', issuer='marketplace', options={'require':['exp','iat','sub','type','jti','role']})
        if claims['type'] != kind or claims['role'] not in ('USER','SELLER','ADMIN'):
            raise jwt.InvalidTokenError()
        claims['sub'] = UUID(claims['sub'])
        claims['jti'] = UUID(claims['jti'])
        return claims
    except jwt.ExpiredSignatureError:
        code = 'TOKEN_EXPIRED' if kind == 'access' else 'REFRESH_TOKEN_INVALID'
    except (jwt.InvalidTokenError,ValueError,TypeError):
        code = 'TOKEN_INVALID' if kind == 'access' else 'REFRESH_TOKEN_INVALID'
    raise BusinessError(code,401,'Токен недействителен или истёк')


def require_roles(*roles):
    async def dependency(request: Request):
        credentials = await bearer(request)
        if credentials is None:
            raise BusinessError('TOKEN_INVALID',401,'Требуется access token')
        claims = decode_token(credentials.credentials,'access')
        request.state.user_id = str(claims['sub'])
        if claims['role'] not in roles:
            raise BusinessError('ACCESS_DENIED',403,'Недостаточно прав')
        return {'id':claims['sub'],'role':claims['role']}
    return dependency


def issue_tokens(db, user):
    now = datetime.now(timezone.utc)
    common = {'sub':str(user['id']),'role':user['role'],'iat':now,'iss':'marketplace','aud':'marketplace'}
    refresh_jti = uuid4()
    refresh_exp = now + timedelta(days=settings.REFRESH_DAYS)
    access = jwt.encode({**common,'type':'access','jti':str(uuid4()),'exp':now + timedelta(minutes=settings.ACCESS_MINUTES)},settings.JWT_SECRET,algorithm='HS256')
    refresh = jwt.encode({**common,'type':'refresh','jti':str(refresh_jti),'exp':refresh_exp},settings.JWT_SECRET,algorithm='HS256')
    db.execute(insert(refresh_tokens).values(jti=refresh_jti,user_id=user['id'],expires_at=refresh_exp,revoked=False))
    return {'access_token':access,'refresh_token':refresh,'token_type':'bearer','expires_in':settings.ACCESS_MINUTES * 60}
