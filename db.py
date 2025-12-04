# db.py
import psycopg2
import os
from dotenv import load_dotenv
import streamlit as st
from psycopg2 import sql 
import pandas as pd 
import re # لإزالة الأحرف غير الرقمية

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

# قائمة الأسماء الحقيقية للأعمدة في قاعدة البيانات (يجب أن تطابق الأعمدة في PostgreSQL)
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
        if not DB_URL:
            st.error("❌ متغير DATABASE_URL غير موجود. يرجى مراجعة ملف .env")
            return None
        conn = psycopg2.connect(DB_URL, sslmode='require') 
        return conn
    except Exception as e:
        # إظهار رسالة الخطأ للمطور (يمكنك إزالتها في الإنتاج)
        st.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}") 
        return None

def clean_data_type(key, value):
    """تنظيف وتحويل القيم إلى تنسيقات صالحة لـ PostgreSQL."""
    # ب. محاولة التحويل الهجري (للتاريخ الصادر والوارد)
        if Hijri and key in ["تاريخ الصادر", "تاريخ الوارد"]:
            try:
                # تنظيف النص بالكامل من المسافات وعلامات التنقيط باستثناء الشرطة المائلة
                clean_str = date_str.replace('م', '').strip()
                
                parts = clean_str.split('/')
                if len(parts) == 3:
                    # تنظيف الأرقام العربية وتحويلها إلى أعداد صحيحة
                    # استخدام re.sub لتنظيف أي شيء غير الأرقام
                    y, m, d = [int(re.sub(r'[^\d]', '', p)) for p in parts]
                    
                    # 💡 التعديل هنا: محاولة استكمال السنة إذا كانت أرقامها قليلة
                    if len(str(y)) < 4 and y < 1000:
                        y += 1400 # إضافة 1400 لاكتمال السنة الهجرية (مثال 445 تصبح 1445)
                    
                    # التأكد من أن السنة هجرية
                    if y > 1300 and y < 1500: 
                        gregorian_date = Hijri(y, m, d).to_gregorian()
                        return gregorian_date
                    
            except Exception:
                pass
    # 1. التعامل مع القيم الفارغة
    if value is None or value == 'غير متوفر' or value == '' or pd.isna(value):
        return None

    # 2. تحويل الأعمدة الرقمية (NUMERIC)
    numeric_fields = ["رصيد الحساب", "الدخل السنوي", "إجمالي إيداع الدراسة"]
    if key in numeric_fields:
        try:
            # إزالة أي أحرف غير رقمية أو علامات عشرية غير ضرورية
            # مثال: '٦,٣١٦' -> '6316' أو '392,150' -> '392150'
            cleaned_value = str(value).replace('،', '').replace(',', '')
            # إزالة أي رموز غير ضرورية
            cleaned_value = re.sub(r'[^\d\.]', '', cleaned_value)
            
            return float(cleaned_value)
        except ValueError:
            st.warning(f"⚠️ تنبيه: فشل تحويل القيمة '{value}' في حقل '{key}' إلى رقم.")
            return None
            
    # 3. تحويل الأعمدة التاريخية (DATE)
    date_fields = ["تاريخ الصادر", "تاريخ الميلاد الوافد", "تاريخ الدخول", "تاريخ الوارد", "تاريخ الدارسة من", "تاريخ الدراسة الى"]
    if key in date_fields:
        try:
            # محاولة تحويل التاريخ باستخدام pandas (تدعم العديد من التنسيقات الميلادية)
            # إذا كان التاريخ هجرياً، ستحتاج إلى مكتبة تحويل هجري خارجية، وإلا سيفشل
            date_obj = pd.to_datetime(value, errors='ignore', dayfirst=False)
            if pd.notna(date_obj):
                return date_obj.date()
            else:
                return None
        except Exception:
            st.warning(f"⚠️ تنبيه: فشل تحويل القيمة '{value}' في حقل '{key}' إلى تاريخ.")
            return None

    # 4. القيم الأخرى (VARCHAR/TEXT)
    return value


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
            
            # 💡 تنظيف وتحويل القيمة
            processed_value = clean_data_type(key, value)

            # نُدرج الأعمدة والقيم الخاصة بها في القائمة
            insert_columns.append(sql.Identifier(key))
            insert_values.append(sql.Literal(processed_value))
            

        # بناء استعلام INSERT الديناميكي
        columns_sql = sql.SQL(', ').join(insert_columns)
        values_list = sql.SQL(', ').join(insert_values)

        # استخدام sql.SQL لاسم الجدول مع ذكر المخطط (Schema) لزيادة الموثوقية
        insert_query = sql.SQL("""
            INSERT INTO public.تقارير_الاشتباه ({columns})
            VALUES ({values})
        """).format(
            columns=columns_sql,
            values=values_list
        )
        
        cur.execute(insert_query)
        
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        # إظهار رسالة الخطأ الدقيقة لتحديد المشكلة الأخيرة (إن وجدت)
        st.error(f"❌ حدث خطأ أثناء حفظ البيانات: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return False

def fetch_all_reports():
    """يجلب جميع السجلات من جدول تقارير_الاشتباه."""
    conn = connect_db()
    if not conn:
        return None, None 

    try:
        cur = conn.cursor()
        
        # استخدام sql.SQL لاسم الجدول مع ذكر المخطط (Schema)
        select_query = sql.SQL('SELECT * FROM public.تقارير_الاشتباه')
        
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
        return None, None
