import getpass
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from app.db import engine
from app.security import password_hasher
from app.tables import users


def main():
    username = input('Admin username: ').strip()
    password = getpass.getpass('Admin password: ')
    if not 3 <= len(username) <= 100 or not 8 <= len(password) <= 128:
        raise SystemExit('Username: 3–100 characters; password: 8–128 characters')
    with Session(engine) as db, db.begin():
        id = db.scalar(insert(users).values(username=username,password_hash=password_hasher.hash(password),role='ADMIN').on_conflict_do_nothing(index_elements=['username']).returning(users.c.id))
        if id is None:
            raise SystemExit('Username already exists; no changes made')
        print(f'Admin created: {id}')


if __name__ == '__main__':
    main()
