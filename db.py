# db.py

import psycopg2
import os
from dotenv import load_dotenv
import streamlit as st
from psycopg2 import sql # مهم لاستخدام علامات الاقتباس المزدوجة حول الأسماء العربية

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

# قائمة الأسماء الحقيقية للأعمدة في قاعدة البيانات (بما في ذلك الحقول المضافة من app.py)
# تأكد أن هذا الاسم يتطابق مع الاسم الفعلي للعمود المختصر في قاعدتك
DB_COLUMN_NAMES = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي إيداع الدراسة", # الاسم المختصر في قاعدة البيانات
    "اسم الملف", 
    "وقت الاستخلاص"
]

# قائمة مفاتيح Python في القاموس extracted_data (كما تأتي من Gemini ومن app.py)
DATA_KEYS = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي الإيداع على الحساب اثناء الدراسة", # الاسم الطويل كما في قاموس Python
    "اسم الملف", 
    "وقت الاستخلاص"
]

def connect_db():
    try:
        # إضافة sslmode='require' للاتصال الآمن بـ Neon
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
        
        # 1. إعداد البيانات للتمرير وتحويل القيم الفارغة إلى None/NULL
        processed_data = {}
        for key in DATA_KEYS:
            value = extracted_data.get(key)
            # التعامل مع الاسم الطويل: نأخذ القيمة من المفتاح الطويل
            if key == "إجمالي الإيداع على الحساب اثناء الدراسة":
                 db_key = "إجمالي إيداع الدراسة" # نستخدم المفتاح القصير في DB
            else:
                 db_key = key # باقي المفاتيح تتطابق في الاسم القصير والطويل

            # استبدال 'غير متوفر' أو السلاسل الفارغة بـ None (Null في SQL)
            if value == 'غير متوفر' or value == '' or value is None:
                processed_data[db_key] = None
            else:
                processed_data[db_key] = value

        # 2. بناء استعلام INSERT الديناميكي
        
        # قائمة الأعمدة (بين علامات اقتباس مزدوجة)
        columns_sql = [sql.Identifier(col) for col in DB_COLUMN_NAMES]
        
        # قائمة القيم (باستخدام القاموس processed_data)
        values_list = []
        for col_name in DB_COLUMN_NAMES:
            # هنا نستخدم أسماء الأعمدة في قاعدة البيانات (DB_COLUMN_NAMES) لاسترجاع القيمة
            values_list.append(sql.Literal(processed_data.get(col_name)))

        # بناء جملة INSERT النهائية باستخدام اسم الجدول الصحيح
        insert_query = sql.SQL("""
            INSERT INTO "تقارير_الاشتباه" ({columns})
            VALUES ({values})
        """).format(
            columns=sql.SQL(', ').join(columns_sql),
            values=sql.SQL(', ').join(values_list)
        )
        
        # تنفيذ الاستعلام
        cur.execute(insert_query)
        
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"❌ حدث خطأ أثناء حفظ البيانات: {e}")
        # db.py (إضافة دالة جديدة)

# ... (باقي الكود كما هو، دالة connect_db موجودة)

def fetch_all_reports():
    conn = connect_db()
    if not conn:
        return None

    try:
        cur = conn.cursor()
        
        # نستخدم SELECT لجلب جميع الأعمدة من جدول تقارير_الاشتباه
        # ونستخدم sql.Identifier لتغليف اسم الجدول
        select_query = sql.SQL('SELECT * FROM {table_name}').format(
            table_name=sql.Identifier('تقارير_الاشتباه')
        )

        cur.execute(select_query)
        
        # جلب أسماء الأعمدة (رؤوس الجدول)
        column_names = [desc[0] for desc in cur.description]
        
        # جلب جميع الصفوف
        records = cur.fetchall()
        
        cur.close()
        conn.close()
        
        # إرجاع الصفوف وأسماء الأعمدة
        return records, column_names

    except Exception as e:
        st.error(f"❌ حدث خطأ أثناء جلب البيانات من قاعدة البيانات: {e}")
        if conn:
            conn.close()
        return None
        # تراجع عن العملية في حالة الخطأ
        if conn:
            conn.rollback()
            conn.close()
        return False
