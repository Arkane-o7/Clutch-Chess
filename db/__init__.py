import config
from db.service import DbService


db_service = DbService(config.DATABASE_URL)
