# -*- coding: utf-8 -*-
# app.py
import streamlit as st
import pandas as pd
import json
import io
import base64
import os
import re 
import pytz 
from google import genai
from google.genai.errors import APIError
import time # تم إضافة هذا للاستفادة من خاصية إعادة المحاولة
from db import save_to_db, fetch_all_reports

# ===============================
# 1. إعدادات API
# ===============================
# يفضل تحميل هذا من ملف .env في بيئة الإنتاج
# **تنبيه**: يرجى استخدام os.getenv("GEMINI_API_KEY") وتجنب وضع المفتاح مباشرة
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyAnvwxAKUKdzPkHUqPylCYmlWvo4uzFdpQ") 
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'
SYSTEM_PROMPT = (
    "أنت نظام استخلاص بيانات آلي (OCR/NLP). مهمتك هي قراءة النص والصورة المستخرجة من الوثيقة المالية "
    "وتحويل البيانات إلى كائن JSON وفقاً للمخطط المحدد بدقة. يجب عليك **نسخ** جميع القيم المستخلصة "
    "تماما كما تظهر في المستند الأصلي، دون تلخيص أو إعادة صياغة، خاصةً في حقل 'سبب الاشتباه'. "
    "قم بتصحيح أي انعكاس أو تشويش في النص العربي قبل الاستخلاص. استخدم القيمة 'غير متوفر' للحقول غير الموجودة."
)

REPORT_FIELDS_ARABIC = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي إيداع الدراسة"
]

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        field: {"type": "STRING", "description": f"القيمة المستخلصة لـ: {field}"}
        for field in REPORT_FIELDS_ARABIC
    },
    "propertyOrdering": REPORT_FIELDS_ARABIC
}

# 💡 دالة مساعدة لتحويل الأرقام العربية إلى إنجليزية
def arabic_to_english_numbers(text):
    if not isinstance(text, str):
        return text
    arabic_map = {'٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
                  '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'}
    return text.translate(str.maketrans(arabic_map))

# 💡 دالة التحقق من التشتت (المؤشر)
def check_for_suspicion(data):
    """يضيف علامة 'مؤشر التشتت' (🔴) للبيانات المشكوك فيها."""
    suspicion_indicator = ""
    
    # --- 1. التحقق من التواريخ الهجرية (المثال: 0945/06/20) ---
    date_fields = ["تاريخ الصادر", "تاريخ الوارد"]
    for field in date_fields:
        date_val = data.get(field, "")
        try:
            # تنظيف الأرقام العربية وتحويلها إلى إنجليزية
            date_str_en = arabic_to_english_numbers(str(date_val))
            
            # محاولة استخراج السنة باستخدام فواصل متعددة
            parts = re.split(r'[/\-.]', date_str_en)
            if len(parts) == 3:
                # إزالة أي أحرف غير رقمية من الجزء الأول (السنة)
                year_str = re.sub(r'[^\d]', '', parts[0])
                year = int(year_str) if year_str else 0
                
                # المعيار: إذا كانت السنة الهجرية غير مكتملة أو خارج النطاق 1400-1500
                # هذا الشرط يلتقط الأخطاء مثل قراءة 0945 كـ 945
                if year > 100 and year < 1400: 
                    suspicion_indicator += f"🔴 ({field}: سنة غير طبيعية) "
        except Exception:
            # إذا فشل التحويل بالكامل (مثل القيمة النصية)
            if str(date_val).strip() not in ['غير متوفر', '']:
                 suspicion_indicator += f"🔴 ({field}: صيغة غير مفهومة) "
            pass

    # --- 2. التحقق من القيم المالية المستخلصة كصفر ---
    financial_fields = ["رصيد الحساب", "الدخل السنوي", "إجمالي إيداع الدراسة"]
    for field in financial_fields:
        val = data.get(field, "")
        if str(val).strip() in ['0', '0.00', '٠', '٠,٠٠']:
             suspicion_indicator += f"⚠️ ({field} = 0) "

    return suspicion_indicator.strip() or "✅ سليم"

# ===============================
# 2. وظائف المعالجة (مع خاصية إعادة المحاولة)
# ===============================
def extract_financial_data(file_bytes, file_name, file_type):
    MAX_RETRIES = 3 # تم تعيين الحد الأقصى للمحاولات
    for attempt in range(MAX_RETRIES):
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            mime_type = "application/pdf" if file_type=='pdf' else f"image/{'jpeg' if file_type=='jpg' else file_type}"

            content_parts = [
                "قم باستخلاص جميع البيانات...",
                {"inlineData": {"data": base64.b64encode(file_bytes).decode('utf-8'), "mimeType": mime_type}}
            ]

            config = {
                "systemInstruction": SYSTEM_PROMPT,
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA
            }

            with st.spinner(f"⏳ جاري الاستخلاص من '{file_name}' - المحاولة {attempt + 1} / {MAX_RETRIES}..."):
                response = client.models.generate_content(model=MODEL_NAME, contents=content_parts, config=config)

            extracted_data = json.loads(response.text)
            extracted_data['اسم الملف'] = file_name
            
            # 💡 استخدام المنطقة الزمنية "Asia/Riyadh" (توقيت السعودية)
            riyadh_tz = pytz.timezone('Asia/Riyadh')
            extracted_data['وقت الاستخلاص'] = pd.Timestamp.now(tz=riyadh_tz).strftime("%Y-%m-%d %H:%M:%S")

            # إضافة مؤشر التشتت
            extracted_data['مؤشر التشتت'] = check_for_suspicion(extracted_data) 
            
            st.success(f"✅ تم الاستخلاص من '{file_name}' بنجاح!")
            return extracted_data 

        except APIError as e:
            # 💡 التعامل مع خطأ 503 (Service Unavailable)
            if '503 UNAVAILABLE' in str(e) and attempt < MAX_RETRIES - 1:
                wait_time = 2 ** attempt  # تأخير مضاعف: 1، 2، 4 ثوانٍ
                st.warning(f"⚠️ خطأ مؤقت 503. سيتم إعادة المحاولة بعد {wait_time} ثوانٍ.")
                time.sleep(wait_time)
                continue  # الانتقال إلى المحاولة التالية
            else:
                st.error(f"❌ خطأ أثناء الاستخلاص بعد {attempt + 1} محاولات: {e}")
                return None 
        
        except Exception as e:
            st.error(f"❌ خطأ غير متوقع أثناء الاستخلاص: {e}")
            return None
    
    # في حال فشل جميع المحاولات
    return None

def create_final_report_from_db(records, column_names):
    import xlsxwriter
    if not records: 
        st.warning("لا توجد بيانات في قاعدة البيانات لتصديرها.")
        return None

    df = pd.DataFrame(records, columns=column_names)
    df.insert(0, '#', range(1, len(df) + 1))
    
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    
    # تصحيح الخطأ: استخدام اسم ورقة عمل لا يتجاوز 31 حرفاً
    sheet_name = 'التقرير المالي النهائي' 
    
    df.to_excel(writer, sheet_name=sheet_name, index=False)
    
    # تنسيق Excel
    workbook, worksheet = writer.book, writer.sheets[sheet_name]
    worksheet.right_to_left()
    col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})
    
    for i, col_name in enumerate(df.columns):
        if col_name == 'سبب الاشتباه':
            worksheet.set_column(i, i, 120, col_format)
        else:
            width = 25 if col_name in ["اسم المشتبه به", "رقم صاحب العمل/ السجل التجاري", "اسم الملف", "وقت الاستخلاص"] else 18
            worksheet.set_column(i, i, width, col_format)
            
    writer.close()
    output.seek(0)
    return output.read()

import streamlit as st
import pandas as pd

# ===============================
# 1. تنسيق الواجهة (CSS)
# ===============================
st.markdown(
    """
    <style>
    /* خلفية عامة */
    .stApp {
        background-color: #f5f7fa;
        font-family: "Tajawal", sans-serif;
    }

    /* العناوين */
    h1, h2, h3 {
        color: #1a3c6e !important;
        font-weight: 700 !important;
    }

    /* تنسيق الخط */
    p, div, span {
        font-size: 16px !important;
    }

    /* الأزرار */
    .stButton button {
        background-color: #1a3c6e !important;
        color: white !important;
        border-radius: 10px !important;
        padding: 10px 25px !important;
        font-size: 17px !important;
        transition: 0.3s;
    }
    .stButton button:hover {
        background-color: #102649 !important;
        transform: scale(1.05);
    }

    /* الجدول */
    .stDataFrame table {
        border-radius: 10px !important;
    }
    .dataframe tbody tr:nth-child(odd) {
        background-color: #eef2f7 !important;
    }
    .dataframe tbody tr:hover {
        background-color: #d7e3ff !important;
    }

    </style>
    """,
    unsafe_allow_html=True
)


# ===============================
# 2. واجهة المستخدم
# ===============================
def main():

    st.set_page_config(layout="wide", page_title="أداة استخلاص وتقارير مالية")

    st.title("نظام ذكي لاستخلاص البيانات ")
    st.markdown("---")

    uploaded_files = st.file_uploader(
        "📤 قم بتحميل الملفات (يمكنك اختيار عدة ملفات)",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True
    )

    # حفظ البيانات
    if uploaded_files:
        all_extracted_data = []

        if 'extracted_data_df' not in st.session_state:
            st.session_state['extracted_data_df'] = pd.DataFrame()

        if st.button("🚀 بدء الاستخلاص"):
            for uploaded_file in uploaded_files:
                file_bytes, file_name = uploaded_file.read(), uploaded_file.name
                file_type = file_name.split('.')[-1].lower()
                st.info(f"⏳ جاري معالجة الملف: **{file_name}**")

                data = extract_financial_data(file_bytes, file_name, file_type)

                if data:
                    all_extracted_data.append(data)

            if all_extracted_data:
                new_df = pd.DataFrame(all_extracted_data)

                # الأعمدة المعروضة
                display_cols = ["مؤشر التشتت", "اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
                new_df = new_df.reindex(columns=display_cols, fill_value='غير متوفر')

                st.session_state['extracted_data_df'] = pd.concat(
                    [st.session_state['extracted_data_df'], new_df],
                    ignore_index=True
                )

    # ======================================================
    # 📋 جدول البيانات بعد الاستخلاص + قابل للتعديل
    # ======================================================
    if not st.session_state['extracted_data_df'].empty:
        st.subheader("✏️ جميع البيانات المستخلصة (قابلة للتعديل)")

        edited_df = st.data_editor(
            st.session_state['extracted_data_df'],
            use_container_width=True,
            num_rows="dynamic"
        )

        st.markdown("---")

        # ----------------------------
# قسم: تحليل سريع وسهل للبيانات
# ----------------------------
import matplotlib.pyplot as plt
from io import BytesIO

def safe_to_numeric(series):
    """حوّل قيم (قد تكون بالعربية) إلى أرقام float بأمان."""
    def conv(v):
        if pd.isna(v): 
            return None
        s = str(v).strip()
        s = arabic_to_english_numbers(s)
        # إزالة فواصل آلاف شائعة (، ,) واستبدال الفاصلة العشرية إن وجدت
        s = s.replace(',', '').replace('،', '')
        s = s.replace('٫', '.').replace(' ', '')
        # إزالة أي رموز عملة
        s = re.sub(r'[^\d.\-]', '', s)
        try:
            return float(s) if s != '' else None
        except:
            return None
    return series.apply(conv)

if 'extracted_data_df' in st.session_state and not st.session_state['extracted_data_df'].empty:
    df_for_analysis = st.session_state['extracted_data_df'].copy()

    st.markdown("---")
    st.subheader("📈 تحليل سريع ومبسط للبيانات")

    # ------- مؤشرات سريعة (KPI) -------
    total_records = len(df_for_analysis)
    total_files = df_for_analysis['اسم الملف'].nunique() if 'اسم الملف' in df_for_analysis.columns else 'غير معروف'
    # حساب المشكوك فيه من مؤشر التشتت إذا موجود
    if 'مؤشر التشتت' in df_for_analysis.columns:
        suspicious_mask = df_for_analysis['مؤشر التشتت'].astype(str).str.contains('🔴|⚠️')
        suspicious_count = suspicious_mask.sum()
    else:
        suspicious_count = 0

    k1, k2, k3 = st.columns(3)
    k1.metric("إجمالي السجلات", total_records)
    k2.metric("عدد الملفات", total_files)
    k3.metric("سجلات مشكوك فيها", suspicious_count)

    st.markdown("")

    # ------- رسم: حالة مؤشر التشتت (دائري) -------
    if 'مؤشر التشتت' in df_for_analysis.columns:
        status_counts = df_for_analysis['مؤشر التشتت'].fillna('غير متوفر').value_counts()
        fig1, ax1 = plt.subplots(figsize=(4,4))
        ax1.pie(status_counts.values, labels=status_counts.index, autopct='%1.1f%%', startangle=90, wedgeprops={'edgecolor': 'white'})
        ax1.axis('equal')
        st.pyplot(fig1)
        plt.close(fig1)

    # ------- رسم: أكثر الجنسيات (شريطي) -------
    if 'الجنسية' in df_for_analysis.columns:
        top_nationalities = df_for_analysis['الجنسية'].fillna('غير معروف').value_counts().nlargest(8)
        fig2, ax2 = plt.subplots(figsize=(7,4))
        top_nationalities.plot(kind='bar', ax=ax2)
        ax2.set_title("أكثر الجنسيات ظهوراً")
        ax2.set_xlabel("")
        ax2.set_ylabel("عدد السجلات")
        plt.tight_layout()
        st.pyplot(fig2)
        plt.close(fig2)

    # ------- رسم: توزيع رصيد الحساب (هيستوغرام) -------
    if 'رصيد الحساب' in df_for_analysis.columns:
        numeric_balance = safe_to_numeric(df_for_analysis['رصيد الحساب'])
        if numeric_balance.dropna().empty:
            st.info("لا توجد قيم رقمية لعمود 'رصيد الحساب' لعرض الرسم.")
        else:
            fig3, ax3 = plt.subplots(figsize=(7,4))
            ax3.hist(numeric_balance.dropna(), bins=20)
            ax3.set_title("توزيع رصيد الحساب")
            ax3.set_xlabel("الرصيد")
            ax3.set_ylabel("تردد")
            plt.tight_layout()
            st.pyplot(fig3)
            plt.close(fig3)

    # ------- جدول: أعلى 10 سجلات مشكوك فيها -------
    if 'مؤشر التشتت' in df_for_analysis.columns:
        suspicious_df = df_for_analysis[suspicious_mask].copy()
        if not suspicious_df.empty:
            st.markdown("**⚠️ ملخص السجلات المشكوك فيها (أعلى 10):**")
            st.dataframe(suspicious_df.head(10))
        else:
            st.info("لا توجد سجلات مشكوك فيها لعرضها.")

    # ------- زر لتحميل ملخص التحليل كملف CSV -------
    summary = {
        "إجمالي السجلات": [total_records],
        "عدد الملفات": [total_files],
        "سجلات مشكوك فيها": [suspicious_count]
    }
    summary_df = pd.DataFrame(summary)

    csv_buffer = BytesIO()
    combined_for_export = {
        "summary": summary_df,
        "top_nationalities": df_for_analysis['الجنسية'].value_counts().head(20) if 'الجنسية' in df_for_analysis.columns else pd.Series(dtype=int),
        "suspicious_samples": suspicious_df.head(50) if 'الجنسية' in df_for_analysis.columns else pd.DataFrame()
    }
    # لتصدير: سنصدر فقط summary و top_nationalities و أول 50 مشكوك
    # نُصدر كـ CSV واحد (summary + top nationalities + suspects)
    export_df = pd.DataFrame()
    # إضافة summary
    export_df = pd.concat([export_df, summary_df], axis=1)
    # إضافة top_nationalities في أعمدة إضافية (إن وُجِد)
    if 'الجنسية' in df_for_analysis.columns:
        tn = df_for_analysis['الجنسية'].value_counts().reset_index()
        tn.columns = ['الجنسية', 'العدد']
        # نحرص على تحويلها إلى CSV منفصل بالأسفل
        combined_csv = export_df.to_csv(index=False, encoding='utf-8-sig')
        tn_csv = tn.to_csv(index=False, encoding='utf-8-sig')
        suspects_csv = suspicious_df.head(200).to_csv(index=False, encoding='utf-8-sig')
        full_csv = "### summary\n" + combined_csv + "\n\n### top_nationalities\n" + tn_csv + "\n\n### suspicious_samples\n" + suspects_csv
        st.download_button("⬇️ تحميل ملخص التحليل (CSV)", data=full_csv, file_name="analysis_summary.csv", mime="text/csv")
    else:
        st.download_button("⬇️ تحميل ملخص التحليل (CSV)", data=export_df.to_csv(index=False, encoding='utf-8-sig'), file_name="analysis_summary.csv", mime="text/csv")


    
        # زر الحفظ
        if st.button("💾 تأكيد وحفظ التعديلات في قاعدة البيانات"):
            saved_count = 0
            total_rows = len(edited_df)
            status_placeholder = st.empty()

            for index, row in edited_df.iterrows():
                row_data = dict(row)

                # حذف عمود التشتت
                if 'مؤشر التشتت' in row_data:
                    del row_data['مؤشر التشتت']

                if save_to_db(row_data):
                    saved_count += 1
                else:
                    status_placeholder.error(f"❌ فشل حفظ السجل رقم {index + 1}.")
                    break

            if saved_count == total_rows:
                status_placeholder.success(f"✅ تم حفظ {saved_count} سجل بنجاح!")
                st.session_state['extracted_data_df'] = pd.DataFrame()
                st.rerun()
            else:
                status_placeholder.warning(f"⚠️ تم حفظ {saved_count} فقط. راجع الأخطاء.")


    # ======================================================
    # 📊 قسم التصدير
    # ======================================================
    st.markdown("---")
    st.subheader("📊 تصدير البيانات النهائية")

    if st.button("⬇️ تحميل تقرير Excel"):
        report_data = fetch_all_reports()

        if report_data and report_data[0] is not None:
            records, column_names = report_data

            with st.spinner("📝 جاري إنشاء ملف Excel..."):
                excel_data_bytes = create_final_report_from_db(records, column_names)

            if excel_data_bytes:
                st.download_button(
                    "⬇️ اضغط للتحميل",
                    data=excel_data_bytes,
                    file_name="Final_Database_Report.xlsx",
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
        else:
            st.error("❌ لا توجد بيانات في قاعدة البيانات.")


# تشغيل التطبيق
if __name__ == "__main__":
    main()
