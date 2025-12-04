# db.py (الكود المصحح)
import psycopg2
import os
from dotenv import load_dotenv
import streamlit as st
from psycopg2 import sql 
import pandas as pd # مهم

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

# قائمة الأسماء الحقيقية للأعمدة في قاعدة البيانات
DB_COLUMN_NAMES = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي إيداع الدراسة",
    "اسم الملف", 
    "وقت الاستخلاص"
]

# قائمة مفاتيح Python هي نفسها أسماء الأعمدة
DATA_KEYS = DB_COLUMN_NAMES 

def connect_db():
    try:
        conn = psycopg2.connect(DB_URL, sslmode='require') 
        return conn
    except Exception as e:
        st.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        return None

def save_to_db(extracted_data):
    conn = connect_db()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # إعداد البيانات وتحويل الفارغ إلى None/NULL
        processed_data = {}
        for key in DATA_KEYS:
            value = extracted_data.get(key)
            # التعامل مع أي قيمة فارغة أو غير متوفرة كـ None
            if value == 'غير متوفر' or value == '' or value is None or pd.isna(value):
                processed_data[key] = None
            else:
                processed_data[key] = value

        # 1. بناء قائمة الأعمدة المقتبسة
        columns_sql = sql.SQL(', ').join([sql.Identifier(col) for col in DB_COLUMN_NAMES])
        
        # 2. بناء قائمة القيم الحرفية (Literals)
        values_list = sql.SQL(', ').join([sql.Literal(processed_data.get(key)) for key in DATA_KEYS])

        # بناء جملة INSERT النهائية باستخدام اسم الجدول الصحيح (باستخدام sql.SQL)
        insert_query = sql.SQL("""
            INSERT INTO {table_name} ({columns})
            VALUES ({values})
        """).format(
            table_name=sql.SQL('تقارير_الاشتباه'), # 👈 حل مشكلة الاسم العربي
            columns=columns_sql,
            values=values_list
        )
        
        cur.execute(insert_query)
        
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"❌ حدث خطأ أثناء حفظ البيانات: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return False

# دالة جلب كل البيانات من قاعدة البيانات
def fetch_all_reports():
    conn = connect_db()
    if not conn:
        return None

    try:
        cur = conn.cursor()
        
        select_query = sql.SQL('SELECT * FROM {table_name}').format(
            table_name=sql.SQL('تقارير_الاشتباه') # 👈 حل مشكلة الاسم العربي
        )

        cur.execute(select_query)
        
        column_names = [desc[0] for desc in cur.description]
        records = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return records, column_names

    except Exception as e:
        st.error(f"❌ حدث خطأ أثناء جلب البيانات من قاعدة البيانات: {e}")
        if conn:
            conn.close()
        return None
