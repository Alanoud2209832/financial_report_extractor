# db.py
import os
import psycopg2
from dotenv import load_dotenv
import streamlit as st

load_dotenv()

def connect_db():
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        return conn
    except Exception as e:
        print("❌ فشل الاتصال بقاعدة البيانات:", e)
        return None

def save_to_db(extracted_data):
    conn = connect_db()
    if not conn:
        st.error("❌ فشل الاتصال بقاعدة البيانات. لم يتم حفظ البيانات.")
        return False

    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO suspects (
                رقم_الصادر, تاريخ_الصادر, اسم_المشتبه_به, رقم_الهوية,
                الجنسية, تاريخ_الميلاد_الوافد, تاريخ_الدخول, الحالة_الاجتماعية,
                المهنة, رقم_الجوال, المدينة, رصيد_الحساب, الدخل_السنوي,
                رقم_الوارد, تاريخ_الوارد, رقم_صاحب_العمل, سبب_الاشتباه,
                تاريخ_الدراسة_من, تاريخ_الدراسة_الى, اجمالي_الايداع,
                اسم_الملف, وقت_الاستخلاص
            ) VALUES (
                %(رقم الصادر)s, %(تاريخ الصادر)s, %(اسم المشتبه به)s, %(رقم الهوية)s,
                %(الجنسية)s, %(تاريخ الميلاد الوافد)s, %(تاريخ الدخول)s, %(الحالة الاجتماعية)s,
                %(المهنة)s, %(رقم الجوال)s, %(المدينة)s, %(رصيد الحساب)s, %(الدخل السنوي)s,
                %(رقم_الوارد)s, %(تاريخ_الوارد)s, %(رقم صاحب العمل/ السجل التجاري)s, %(سبب الاشتباه)s,
                %(تاريخ الدارسة من)s, %(تاريخ الدراسة الى)s, %(إجمالي الإيداع على الحساب اثناء الدراسة)s,
                %(اسم الملف)s, %(وقت الاستخلاص)s
            )
        """, extracted_data)
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"❌ حدث خطأ أثناء حفظ البيانات: {e}")
        return False
