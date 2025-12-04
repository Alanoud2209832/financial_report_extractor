# db.py
import psycopg2
import os
from dotenv import load_dotenv
import streamlit as st
from psycopg2 import sql 
import pandas as pd 

load_dotenv()
# تأكد من أن هذا المتغير تم تعريفه في ملف .env
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

DATA_KEYS = DB_COLUMN_NAMES 

def connect_db():
    """ينشئ اتصالًا بقاعدة البيانات."""
    try:
        # تأكد من أن DB_URL متوفر
        if not DB_URL:
            st.error("❌ متغير DATABASE_URL غير موجود. يرجى مراجعة ملف .env")
            return None
        conn = psycopg2.connect(DB_URL, sslmode='require') 
        return conn
    except Exception as e:
        st.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        return None

def save_to_db(extracted_data):
    """يحفظ البيانات المستخلصة إلى جدول تقارير_الاشتباه."""
    conn = connect_db()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        insert_columns = []
        insert_values = []
        
        # بناء قائمة الأعمدة والقيم لتضمينها في استعلام INSERT
        for key in DATA_KEYS:
            value = extracted_data.get(key)
            
            # إذا كانت القيمة فارغة ('غير متوفر'، None، pd.NA، أو سلسلة فارغة)، يتم إرسالها كـ NULL
            if value is None or value == 'غير متوفر' or value == '' or pd.isna(value):
                processed_value = None
            else:
                processed_value = value

            # نُدرج الأعمدة والقيم الخاصة بها في القائمة
            insert_columns.append(sql.Identifier(key))
            insert_values.append(sql.Literal(processed_value))
            

        # بناء استعلام INSERT الديناميكي
        columns_sql = sql.SQL(', ').join(insert_columns)
        values_list = sql.SQL(', ').join(insert_values)

        # استخدام sql.SQL لاسم الجدول (لحل مشكلة الاسم العربي)
        insert_query = sql.SQL("""
            INSERT INTO public.تقارير_الاشتباه ({columns})
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
        st.error(f"❌ حدث خطأ أثناء حفظ البيانات: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return False

def fetch_all_reports():
    """يجلب جميع السجلات من جدول تقارير_الاشتباه."""
    conn = connect_db()
    if not conn:
        return None, None # إرجاع None, None عند فشل الاتصال

    try:
        cur = conn.cursor()
        
        # استخدام sql.SQL لاسم الجدول
        select_query = sql.SQL('SELECT * FROM public.تقارير_الاشتباه')
        
        cur.execute(select_query)
        
        column_names = [desc[0] for desc in cur.description]
        records = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return records, column_names

    except Exception as e:
        # ⚠️ هذا هو المكان الذي يظهر فيه خطأ "relation does not exist"
        st.error(f"❌ حدث خطأ أثناء جلب البيانات من قاعدة البيانات: {e}")
        if conn:
            conn.close()
        return None, None # إرجاع None, None عند الفشل
