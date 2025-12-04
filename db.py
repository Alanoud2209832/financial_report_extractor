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
        insert_columns = []
        insert_values = []
        
        # نمر على البيانات المستخلصة فقط إذا كانت غير فارغة
        for key in DATA_KEYS: # DATA_KEYS هي DB_COLUMN_NAMES 
            value = extracted_data.get(key)
            
            # 💡 يتم إهمال الأعمدة الفارغة تماماً من استعلام INSERT
            if value is None or value == 'غير متوفر' or value == '' or pd.isna(value):
                # إذا كانت القيمة فارغة، نضعها None ليتم تحويلها إلى NULL في SQL
                processed_data[key] = None
            else:
                processed_data[key] = value

            # نبني قائمة الأعمدة والقيم فقط للعناصر غير الفارغة (للسماح بالقيم الافتراضية)
            insert_columns.append(sql.Identifier(key))
            insert_values.append(sql.Literal(processed_data.get(key)))
            

        # بناء استعلام INSERT الديناميكي
        columns_sql = sql.SQL(', ').join(insert_columns)
        values_list = sql.SQL(', ').join(insert_values)

        # بناء جملة INSERT النهائية باستخدام اسم الجدول الصحيح
        insert_query = sql.SQL("""
            INSERT INTO {table_name} ({columns})
            VALUES ({values})
        """).format(
            table_name=sql.SQL('تقارير_الاشتباه'), 
            columns=columns_sql,
            values=values_list
        )
        
        cur.execute(insert_query)
        
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        # ⚠️ الآن يجب أن يظهر هذا الخطأ تفاصيل المشكلة (مثل خطأ في التاريخ أو الرقم)
        st.error(f"❌ حدث خطأ أثناء حفظ البيانات: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return False
