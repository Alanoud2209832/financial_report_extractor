import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()  # تحميل متغيرات .env

def connect_db():
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        print("🚀 Connected to Neon Database Successfully!")
        return conn
    except Exception as e:
        print("❌ Connection Failed:", e)
