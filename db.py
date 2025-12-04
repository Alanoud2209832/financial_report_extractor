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

def clean_data_type(key, value):
    """تنظيف وتحويل القيم إلى تنسيقات صالحة قبل إرسالها إلى قاعدة البيانات."""
    
    # التعامل مع القيم الفارغة أو غير المتوفرة (وهو ما نقوم به بالفعل)
    if value is None or value == 'غير متوفر' or value == '' or pd.isna(value):
        return None

    # 1. تحويل الأعمدة الرقمية (NUMERIC)
    numeric_fields = ["رصيد الحساب", "الدخل السنوي", "إجمالي إيداع الدراسة"]
    if key in numeric_fields:
        try:
            # إزالة علامات الفاصلة العربية أو الأجنبية، واستبدال الفاصلة العشرية بنقطة (للنظام الأمريكي)
            # ثم تحويل النص إلى رقم Pythonي (float)
            cleaned_value = str(value).replace('،', '').replace(',', '').replace('.', '', str(value).count('.') - 1) 
            return float(cleaned_value)
        except ValueError:
            st.warning(f"⚠️ تنبيه: فشل تحويل القيمة '{value}' في حقل '{key}' إلى رقم.")
            return None # العودة بـ None لتجنب خطأ SQL
            
    # 2. تحويل الأعمدة التاريخية (DATE)
    date_fields = ["تاريخ الصادر", "تاريخ الميلاد الوافد", "تاريخ الدخول", "تاريخ الوارد", "تاريخ الدارسة من", "تاريخ الدراسة الى"]
    if key in date_fields:
        # نحن نفترض هنا أن البيانات المستخلصة هي بالتقويم الميلادي بتنسيق YYYY/MM/DD أو YYYY-MM-DD
        # إذا كانت هجرية، ستحتاج إلى مكتبة تحويل مثل hijri_converter (معقد حاليًا)
        try:
            # محاولة تحويل التاريخ مباشرة
            return pd.to_datetime(value, errors='ignore').date()
        except Exception:
            # إذا فشل التحويل (مثل إذا كان التاريخ هجري)، نرجع None
            st.warning(f"⚠️ تنبيه: فشل تحويل القيمة '{value}' في حقل '{key}' إلى تاريخ.")
            return None

    # 3. القيم الأخرى (VARCHAR/TEXT)
    return value

# تعديل دالة save_to_db
def save_to_db(extracted_data):
    conn = connect_db()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        insert_columns = []
        insert_values = []
        
        for key in DATA_KEYS:
            value = extracted_data.get(key)
            
            # 💡 نستخدم الدالة الجديدة لتنظيف وتحويل القيمة
            processed_value = clean_data_type(key, value)

            # ... (بقية الدالة كما هي)
            
            insert_columns.append(sql.Identifier(key))
            insert_values.append(sql.Literal(processed_value))
            

        # بناء استعلام INSERT الديناميكي
        columns_sql = sql.SQL(', ').join(insert_columns)
        values_list = sql.SQL(', ').join(insert_values)
        
        # ... (بناء insert_query وتنفيذه كما هو في الكود السابق)
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
