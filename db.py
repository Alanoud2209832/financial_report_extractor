import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()  # تحميل متغيرات البيئة من .env

def connect_db():
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        return conn
    except Exception as e:
        print("❌ فشل الاتصال بقاعدة البيانات:", e)
        return None

