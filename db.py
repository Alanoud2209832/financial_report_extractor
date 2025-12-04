# db.py (الكود المعدل)

import psycopg2
import os
from dotenv import load_dotenv
import streamlit as st
from psycopg2 import sql # استيراد وحدة sql للمساعدة في التعامل مع المعرفات المعقدة

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

# قائمة الأسماء الحقيقية للأعمدة في قاعدة البيانات (كما تم إنشاؤها بين علامات الاقتباس)
# مهم: تم تعديل الاسم الطويل إلى "إجمالي إيداع الدراسة"
DB_COLUMN_NAMES = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    # يجب أن يتطابق هذا الاسم مع الاسم الذي استخدمته لإنشاء العمود
    "إجمالي إيداع الدراسة", 
    "اسم الملف", 
    "وقت الاستخلاص"
]

# قائمة مفاتيح Python في القاموس extracted_data
# تم تعديل الاسم الطويل ليطابق الاسم الذي يمرره DataFrame (الموجود في app.py)
DATA_KEYS = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي الإيداع على الحساب اثناء الدراسة", # يجب أن يكون هذا هو المفتاح في قاموس Python
    "اسم الملف", 
    "وقت الاستخلاص"
]


def connect_db():
    try:
        # بعض الاتصالات قد تحتاج إلى تحديد sslmode=require
        conn = psycopg2.connect(DB_URL, sslmode='require') 
        return conn
    except Exception as e:
        st.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        return None


def save_to_db(extracted_data):
    conn = connect_db()
    if not conn:
        st.error("❌ فشل الاتصال بقاعدة البيانات. لم يتم حفظ البيانات.")
        return False
    
    # 1. إعداد البيانات للتمرير (معالجة الاسم الطويل ونوع البيانات)
    # ننشئ قاموساً جديداً للوسائط يتطابق مفتاحه مع الحقل في Python (DATA_KEYS)
    # ونقوم بتعديل القيمة الطويلة لتمريرها بشكل صحيح
    
    # يجب التأكد من أن المفتاح في القاموس يتطابق مع القائمة DATA_KEYS
    # الاسم الطويل (كمفتاح في قاموس Python) يجب أن يتطابق مع ما تنتجه دالة Gemini
    # الاسم القصير (كعمود في SQL) يجب أن يتطابق مع ما تم إنشاؤه في قاعدة البيانات

    # إذا كان الحقل في app.py لا يزال يستخدم الاسم الطويل:
    if "إجمالي الإيداع على الحساب اثناء الدراسة" in extracted_data:
        # نقوم بإنشاء نسخة نختصر فيها الاسم ليتطابق مع ما هو مطلوب في Psycopg2
        # (عادةً لا نحتاج لهذه الخطوة لو كنا نستخدم الأسماء الإنجليزية)
        data_to_save = extracted_data.copy()
        
        # 2. تحويل البيانات (التواريخ والأرقام)
        # لضمان عدم تمرير السلسلة "غير متوفر" إلى حقول رقمية/تاريخية
        processed_data = {}
        for key in DATA_KEYS:
            value = data_to_save.get(key)
            # استبدال 'غير متوفر' بـ None ليتم التعامل معها كـ NULL في SQL
            if value == 'غير متوفر' or value == '' or value is None:
                processed_data[key] = None
            else:
                processed_data[key] = value

    # 3. بناء استعلام INSERT الديناميكي
    # لضمان وضع علامات الاقتباس المزدوجة حول الأسماء العربية في SQL

    # بناء قائمة الأعمدة (بين علامات اقتباس مزدوجة)
    columns_sql = [sql.Identifier(col) for col in DB_COLUMN_NAMES]
    
    # بناء قائمة المتغيرات (%s أو %(key)s)
    values_placeholders = [sql.Literal(processed_data.get(key)) for key in DATA_KEYS]

    # بناء جملة INSERT النهائية
    insert_query = sql.SQL("""
        INSERT INTO "تقارير_الاشتباه" ({columns})
        VALUES ({values})
    """).format(
        columns=sql.SQL(', ').join(columns_sql),
        values=sql.SQL(', ').join(values_placeholders)
    )

    try:
        cur = conn.cursor()
        
        # تنفيذ الاستعلام
        cur.execute(insert_query)
        
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"❌ حدث خطأ أثناء حفظ البيانات: {e}")
        conn.rollback()
        conn.close()
        return False

# تأكد من أنك تستخدم هذا الكود الجديد في ملف db.py 
# مع استخدام اسم جدول صحيح (مثلاً "تقارير_الاشتباه") في قاعدة البيانات.
