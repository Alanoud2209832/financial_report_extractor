# db.py
import psycopg2
import os
from dotenv import load_dotenv
import streamlit as st
from psycopg2 import sql
import pandas as pd
import re
from itertools import permutations 
import datetime 

# محاولة استيراد مكتبة التحويل الهجري
try:
    from hijri_converter import Hijri
except ImportError:
    # لا نوقف التنفيذ، فقط نترك تحذير
    Hijri = None

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

# قائمة الأعمدة النهائية في قاعدة البيانات (تم إضافة "رقم الدلالة")
DB_COLUMN_NAMES = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي إيداع الدراسة",
    "رقم الدلالة", 
    "اسم الملف",
    "وقت الاستخلاص"
]

DATA_KEYS = DB_COLUMN_NAMES

# دالة مساعدة لتحويل الأرقام العربية إلى إنجليزية
def arabic_to_english_numbers(text):
    """تحويل الأرقام العربية إلى إنجليزية لتسهيل المعالجة."""
    if not isinstance(text, str):
        return text
    
    arabic_map = {
        '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
        '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
    }
    return text.translate(str.maketrans(arabic_map))


def connect_db():
    """ينشئ اتصالًا بقاعدة البيانات."""
    try:
        if not DB_URL:
            return None
        conn = psycopg2.connect(DB_URL, sslmode='require')
        return conn
    except Exception as e:
        st.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        return None

def _convert_hijri_to_date(parts_tuple):
    """
    دالة مساعدة: تحاول تحويل جزء من التاريخ (المفترض أنه سنة، شهر، يوم) إلى تاريخ ميلادي.
    """
    if not Hijri or len(parts_tuple) != 3:
        return None
        
    try:
        y_str, m_str, d_str = [re.sub(r'[^\d]', '', p) for p in parts_tuple]
        y, m, d = int(y_str), int(m_str), int(d_str)
    except ValueError:
        return None

    # معالجة الأخطاء الشائعة في قراءة السنة الهجرية 
    if y < 1000 and y >= 400:
        y += 1000 
    elif y >= 1 and y <= 99:
        if y < 60: 
            y += 1400
        else:
            y += 1300
    
    # تحقق من نطاق السنة الهجرية المعقول
    if y > 1300 and y < 1500:
        if 1 <= m <= 12 and 1 <= d <= 30:
            try:
                gregorian_date = Hijri(y, m, d).to_gregorian()
                return gregorian_date 
            except Exception:
                return None
                
    return None

def clean_data_type(key, value):
    """تنظيف وتحويل القيم إلى تنسيقات صالحة لـ PostgreSQL."""
    
    # 1. التعامل مع القيم الفارغة
    if value is None or value == 'غير متوفر' or value == '' or pd.isna(value):
        return None

    # 2. تحويل الأعمدة الرقمية (NUMERIC/INTEGER)
    numeric_fields = ["رصيد الحساب", "الدخل السنوي", "إجمالي إيداع الدراسة"]
    
    if key in numeric_fields or key == "رقم الدلالة":
        try:
            cleaned_value = arabic_to_english_numbers(str(value))
            
            # منطق رقم الدلالة (يجب أن يكون INTEGER)
            if key == "رقم الدلالة":
                num_str = re.sub(r'[^\d]', '', cleaned_value)
                if not num_str:
                    return None
                num = int(num_str)
                return num if 1 <= num <= 11 else None 
            
            # منطق الأرقام المالية (المتغير)
            temp_val = re.sub(r'[^\d\.,-]', '', cleaned_value)
            last_separator_index = max(temp_val.rfind('.'), temp_val.rfind(','))
            
            if last_separator_index != -1:
                integer_part = temp_val[:last_separator_index]
                decimal_part = temp_val[last_separator_index+1:]
                integer_part = re.sub(r'[,\.]', '', integer_part) 
                
                if len(decimal_part) > 2:
                    final_val = integer_part + decimal_part
                    final_val = re.sub(r'[^\d\.-]', '', final_val)
                    return float(final_val)
                else:
                    final_val = f"{integer_part}.{decimal_part}"
                    final_val = re.sub(r'[^\d\.-]', '', final_val)
                    return float(final_val)
            else:
                final_val = re.sub(r'[^\d\.-]', '', temp_val)
                if not final_val:
                    return None
                return float(final_val)

        except ValueError:
            return None
            
    # 3. تحويل الأعمدة التاريخية (DATE)
    date_fields = ["تاريخ الصادر", "تاريخ الميلاد الوافد", "تاريخ الدخول", "تاريخ الوارد", "تاريخ الدارسة من", "تاريخ الدراسة الى"]
    if key in date_fields:
        
        date_str = arabic_to_english_numbers(str(value))
        clean_str_base = re.sub(r'[^\d/\-.]', '', date_str).strip()
        
        # ⚠️ التعديل الرئيسي: حصر التواريخ المتوقع أن تكون هجرية في الصادر والوارد فقط.
        # هذا يسمح لـ (الدارسة من/الى) بالتحويل الميلادي المباشر أولاً.
        is_hijri_expected = key in ["تاريخ الصادر", "تاريخ الوارد"] 

        # أ. محاولة تحويل ميلادي مباشر
        if not is_hijri_expected: 
            try:
                # يرجى ملاحظة: قد تحتاج إلى تبديل dayfirst=False إلى dayfirst=True إذا كانت تواريخك تأتي بصيغة يوم/شهر/سنة.
                date_obj = pd.to_datetime(clean_str_base, errors='coerce', dayfirst=False) 
                if pd.notna(date_obj) and date_obj.year > 1800:
                    return date_obj.date()
            except Exception:
                pass
        
        # ب. محاولة التحويل الهجري 
        if Hijri:
            try:
                parts = [p for p in re.split(r'[/\-.]', clean_str_base) if p.strip()] 
                
                if len(parts) == 3:
                    possible_orders = set(permutations(parts))

                    for p in possible_orders:
                        result = _convert_hijri_to_date(p)
                        if result:
                            return result
                            
            except Exception as e:
                pass 
        
        if clean_str_base and key in date_fields:
            pass
            
        return None

    # 4. القيم الأخرى (VARCHAR/TEXT)
    return value


def save_to_db(extracted_data):
    """يحفظ البيانات المستخلصة إلى جدول تقارير_الاشتباه."""
    conn = connect_db()
    if not conn:
        return False
        
    processed_data_for_display = {}
    insert_columns = []
    insert_values = []
    
    for key in DATA_KEYS:
        value = extracted_data.get(key)
        
        processed_value = clean_data_type(key, value)
        
        # لعرض بيانات الحفظ فقط
        processed_data_for_display[key] = str(processed_value) if isinstance(processed_value, datetime.date) else processed_value

        insert_columns.append(sql.Identifier(key))
        insert_values.append(sql.Literal(processed_value))

    st.info("✅ هذه هي البيانات النهائية التي سيتم حفظها في قاعدة البيانات:")
    st.json(processed_data_for_display)

    
    try:
        cur = conn.cursor()
        
        columns_sql = sql.SQL(', ').join(insert_columns)
        values_list = sql.SQL(', ').join(insert_values)

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
        # يتم عرض هذا الخطأ في app.py
        if 'does not exist' in str(e) and 'رقم الدلالة' in str(e):
             st.error("💡 ملاحظة: إذا ظهر هذا الخطأ، فتأكد أنك أنشأت عمود 'رقم الدلالة' في جدول PostgreSQL الخاص بك بنوع **INTEGER**.")
        
        st.error(f"❌ فشل الحفظ في قاعدة البيانات: {e}")
        
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
        
        # اختيار جميع الأعمدة المعرفة فقط (تم حذف "id")
        select_columns = sql.SQL(', ').join([sql.Identifier(col) for col in DB_COLUMN_NAMES])

        # الاستعلام لا يطلب عمود "id" الآن
        select_query = sql.SQL('SELECT {columns} FROM public.تقارير_الاشتباه').format(columns=select_columns)
        
        cur.execute(select_query)
        
        # أسماء الأعمدة المعادة هي نفسها DB_COLUMN_NAMES
        column_names = DB_COLUMN_NAMES 
        records = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return records, column_names

    except Exception as e:
        st.error(f"❌ حدث خطأ أثناء جلب البيانات من قاعدة البيانات: {e}")
        if conn:
            conn.close()
        return None, None
