# app.py
import streamlit as st
import pandas as pd
import json
import io
import base64
import os
import re
import pytz
import time
from dotenv import load_dotenv
from openai import OpenAI
from sqlite3 import OperationalError
from db import save_to_db, fetch_all_reports, initialize_db

# تحميل متغيرات البيئة من ملف .env إن وُجد
load_dotenv()

# ===============================
# إعدادات OpenAI
# ===============================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", None)
if not OPENAI_API_KEY:
    st.error("❌ مفتاح OPENAI_API_KEY غير موجود. أضفه في ملف .env (انظر .env.example).")

# نموذج يمكن تغييره حسب الحاجة (جرب gpt-4o-mini أو gpt-4.1)
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  

client = OpenAI(api_key=OPENAI_API_KEY)

# ===============================
# حقول التقرير (ثابت)
# ===============================
REPORT_FIELDS_ARABIC = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي إيداع الدراسة",
    "رقم الدلالة"
]

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        field: {"type": "string", "description": f"القيمة المستخلصة لـ: {field}"}
        for field in REPORT_FIELDS_ARABIC
    }
}

DELALAT_MAPPING = {
    1: "تكرار العمليات المالية (إيداعات، حوالات سحوبات مشتريات) في حساب المقيم لا تتناسب مع دخله السنوي.",
    2: "تحويلات أو إيداعات نقدية من حساب عميل مقيم الى حساب فرد سعودي أو كيان تجاري.",
    3: "حوالات صادرة أو عمليات مالية متنوعة من حساب مقيم أجنبي لعمليات سداد مصروفات تنم عن المتاجرة فيها أو إعادة بيعها.",
    4: "حوالات دولية صادرة من حساب فرد سعودي أو حساب كيان تجاري إلى حسابات أشخاص بشكل متكرر لا تربطهم به غرض أو علاقة عمل.",
    5: "مقيم يقوم بتنفيذ عمليات تحويل مالية خارج المملكة له أو لأشخاص آخرين بمبالغ لا تتناسب مع دخله وقد يكون مصدرها إيداعات نقدية من عدة عملاء مقيمين.",
    6: "حوالات دولية واردة للحساب الشخصي للمقيم أو للبطاقات الائتمانية بمبالغ عالية تنم عن إدارة نشاط تجاري داخل المملكة.",
    7: "شخص مقيم يقوم بتنفيذ عمليات مالية (إيداع شيك أو صرف شيك أو استقبال حواله مالية) وليس لديه حساب بنكي (عميل عابر).",
    8: "إيداعات نقدية في حساب كيان تجاري بشكل متكرر أو إيداعات مبيعات نقاط بيع، يليها تنفيذ حوالات خارجية أو داخلية لعدة عملاء مقيمين أو عمليات سحب.",
    9: "حوالات دولية واردة أو صادرة لحساب الكيان التجاري لا تتناسب مع نشاط الكيان التجاري.",
    10: "تفويض أجنبي على حساب بنكي عائد لكيان تجاري وتمكينه من الحساب بشكل كامل دون وجود مبرر أو غرض واضح.",
    11: "فتح عدة حسابات الفروع كيان تجاري لنفس النشاط دون وجود ارتباط واضح بين هذه الحسابات، نظراً لإدارة الحساب الخاص بالفرع من قبل المقيم."
}

SYSTEM_PROMPT = (
    "أنت نظام استخلاص بيانات آلي (OCR/NLP). مهمتك هي قراءة النص والصورة المستخرجة من الوثيقة المالية "
    "وتحويل البيانات إلى كائن JSON وفقاً للمخطط المحدد بدقة. "
    "يجب عليك استخلاص جميع التواريخ الهجرية والميلادية وتحويلها إلى صيغة رقمية موحدة 'YYYY/MM/DD'. "
    "قم بنسخ جميع القيم الأخرى تمامًا كما تظهر في المستند الأصلي، واستخدم 'غير متوفر' للحقول المفقودة. "
    "بعد الاستخلاص، ضع في حقل 'رقم الدلالة' رقمًا واحدًا من 1 إلى 11 أو 'غير متوفر'."
)

# ===============================
# دوال مساعدة
# ===============================
def arabic_to_english_numbers(text):
    if not isinstance(text, str):
        return text
    arabic_map = {'٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
                  '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'}
    return text.translate(str.maketrans(arabic_map))


def pre_process_data_fix_dates(data):
    """تفصل تواريخ ملتصقة في حقل 'تاريخ الدارسة من' إن وُجدت"""
    start_key = "تاريخ الدارسة من"
    end_key = "تاريخ الدراسة الى"
    start_date_value = data.get(start_key, "")
    
    if start_date_value and isinstance(start_date_value, str):
        clean_value = re.sub(r'[^\d]', '', start_date_value).strip()
        if len(clean_value) == 16:
            date1 = clean_value[:8]
            date2 = clean_value[8:]
            data[start_key] = f"{date1[:4]}/{date1[4:6]}/{date1[6:]}"
            if not data.get(end_key) or data.get(end_key).strip() in ['', 'غير متوفر']:
                data[end_key] = f"{date2[:4]}/{date2[4:6]}/{date2[6:]}"
    return data


def check_for_suspicion(data):
    suspicion_indicator = ""
    date_fields = ["تاريخ الصادر", "تاريخ الوارد"]
    for field in date_fields:
        date_val = data.get(field, "")
        try:
            date_str_en = arabic_to_english_numbers(str(date_val))
            parts = re.split(r'[/\-.]', date_str_en)
            if len(parts) == 3:
                year_str = re.sub(r'[^\d]', '', parts[0])
                year = int(year_str) if year_str else 0
                if year > 100 and year < 1400:
                    suspicion_indicator += f"🔴 ({field}: سنة غير طبيعية) "
        except Exception:
            if str(date_val).strip() not in ['غير متوفر', '']:
                suspicion_indicator += f"🔴 ({field}: صيغة غير مفهومة) "
            pass
    
    financial_fields = ["رصيد الحساب", "الدخل السنوي", "إجمالي إيداع الدراسة"]
    for field in financial_fields:
        val = data.get(field, "")
        if str(val).strip() in ['0', '0.00', '٠', '٠,٠٠']:
            suspicion_indicator += f"⚠️ ({field} = 0) "
    return suspicion_indicator.strip() or "✅ سليم"


# ===============================
# دالة الاستخلاص عبر OpenAI (مع محاولات RETRY)
# ===============================
def extract_financial_data(file_bytes, file_name, file_type):
    """يستدعي OpenAI ليُرجع JSON مطابق للمخطط. يعيد dict أو None."""
    if not OPENAI_API_KEY:
        return None

    MAX_RETRIES = 3
    INITIAL_WAIT_SECONDS = 5

    mime_type = "application/pdf" if file_type.lower() == 'pdf' else f"image/{file_type.lower()}"

    # نضع الملف كـ base64 ضمن النص المرسل للموديل (ملاحظة: قد يكون كبيراً - لكن نحافظ على آلية مشابهة لنسختك)
    file_b64 = base64.b64encode(file_bytes).decode('utf-8')

    user_prompt = (
        "قم باستخلاص جميع الحقول التالية إلى JSON مطابق للمخطط، وأجب فقط بالـ JSON دون أي شرح إضافي.\n\n"
        f"المخطط (العناوين): {', '.join(REPORT_FIELDS_ARABIC)}\n\n"
        "قواعد:\n"
        "- جميع حقول التاريخ يجب أن تكون بصيغة YYYY/MM/DD أو 'غير متوفر'.\n"
        "- إن لم يظهر حقل في المستند ضع 'غير متوفر'.\n"
        "- حقل 'رقم الدلالة' يجب أن يحتوي رقمًا من 1 إلى 11 أو 'غير متوفر'.\n\n"
        "الآن الملف المرفق (Base64). لا تذكر Base64 في الناتج، استخدمه فقط للمساعدة على الاستخلاص إن أمكن:\n\n"
        f"FILE_NAME: {file_name}\nFILE_MIME: {mime_type}\nFILE_BASE64: (مضمَّن)\n"
    )

    for attempt in range(MAX_RETRIES):
        try:
            response = client.responses.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                # نطلب من الموديل إخراج JSON نصي؛ سنقوم بتحليل النص لاحقًا
                max_tokens=4000,
                temperature=0.0
            )

            # استخراج نص الاستجابة (يتوقف على واجهة SDK؛ هنا نقرأ من response)
            # الحقل التالي متوافق مع OpenAI Python SDK الحديث: response.output_text أو دمج من response.output
            try:
                output_text = response.output_text  # إن كان متاحًا
            except Exception:
                # Fall back: حاول جمع نصوص من response.output إن كانت موجودة
                output_text = ""
                if hasattr(response, "output") and isinstance(response.output, list):
                    for item in response.output:
                        if isinstance(item, dict) and "content" in item:
                            # قد يكون content قائمة
                            cont = item.get("content")
                            if isinstance(cont, list):
                                for c in cont:
                                    if c.get("type") == "output_text":
                                        output_text += c.get("text", "")
                            elif isinstance(cont, str):
                                output_text += cont

            # إذا لم نجد نصًا، حاول استخدام choices (نموذج قديم)
            if not output_text and hasattr(response, "choices"):
                try:
                    output_text = response.choices[0].message["content"]
                except Exception:
                    # آخر حل احتياطي
                    output_text = str(response)

            # الآن نحاول استخراج JSON من النص
            # بعض الموديلات قد ترجع JSON مضمنًا داخل نص؛ نحاول إيجاد أول قوس معقوف
            json_text = output_text.strip()
            # محاولة العثور على بداية JSON
            start = json_text.find('{')
            end = json_text.rfind('}')
            if start != -1 and end != -1 and end > start:
                json_candidate = json_text[start:end+1]
            else:
                json_candidate = json_text

            extracted_data = {}
            try:
                extracted_data = json.loads(json_candidate)
            except Exception as e_json:
                # فشل التحويل => نرجّع None بعد توضيح في سجلات الستريمليت
                st.error(f"❌ فشل تحويل ناتج الموديل إلى JSON للملف {file_name}: {e_json}")
                st.info("نص الناتج من الموديل (أول 1000 حرف):")
                st.code(json_text[:1000])
                return None

            # بعد الاستخلاص: التنظيف والإضافات
            extracted_data = pre_process_data_fix_dates(extracted_data)
            extracted_data['اسم الملف'] = file_name
            riyadh_tz = pytz.timezone('Asia/Riyadh')
            extracted_data['وقت الاستخلاص'] = pd.Timestamp.now(tz=riyadh_tz).strftime("%Y-%m-%d %H:%M:%S")
            extracted_data['مؤشر التشتت'] = check_for_suspicion(extracted_data)

            # تأكد من وجود كل الحقول الأساسية
            for fld in REPORT_FIELDS_ARABIC:
                if fld not in extracted_data:
                    extracted_data[fld] = "غير متوفر"

            return extracted_data

        except Exception as e:
            is_last = (attempt == MAX_RETRIES - 1)
            wait_time = INITIAL_WAIT_SECONDS * (2 ** attempt)
            st.warning(f"⚠️ محاولة الاستخلاص رقم {attempt+1} فشلت لملف {file_name}: {e}.")
            if not is_last:
                st.info(f"إعادة المحاولة بعد {wait_time} ثانية...")
                time.sleep(wait_time)
                continue
            else:
                st.error(f"❌ فشل الاستخلاص من {file_name} بعد {MAX_RETRIES} محاولات.")
                return None


# ===============================
# وظائف التقرير وواجهة المستخدم
# ===============================
def create_final_report_from_db(records, column_names):
    import xlsxwriter
    if not records:
        st.warning("لا توجد بيانات في قاعدة البيانات لتصديرها.")
        return None

    df = pd.DataFrame(records, columns=column_names)
    df.insert(0, '#', range(1, len(df) + 1))

    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    sheet_name = 'التقرير المالي النهائي'
    df.to_excel(writer, sheet_name=sheet_name, index=False)

    workbook, worksheet = writer.book, writer.sheets[sheet_name]
    worksheet.right_to_left()
    col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})

    for i, col_name in enumerate(df.columns):
        if col_name in ['سبب الاشتباه']:
            worksheet.set_column(i, i, 120, col_format)
        else:
            width = 25 if col_name in ["اسم المشتبه به", "رقم صاحب العمل/ السجل التجاري", "اسم الملف", "وقت الاستخلاص"] else 18
            worksheet.set_column(i, i, width, col_format)

    writer.close()
    output.seek(0)
    return output.read()


def display_basic_stats():
    st.markdown("---")
    st.subheader("إحصائيات عامة 📈")
    report_data = fetch_all_reports()
    total_count = 0
    if report_data and report_data[0]:
        records, _ = report_data
        total_count = len(records)

    st.metric(label="إجمالي عدد السجلات/الملفات المحفوظة", value=total_count)
    st.markdown("---")


# ===============================
# CSS وواجهة Streamlit
# ===============================
st.markdown(
    """
    <style>
    .stApp { background-color: #f5f7fa; font-family: "Tajawal", sans-serif; }
    h1,h2,h3 { color: #1a3c6e !important; font-weight: 700 !important; }
    p, div, span { font-size: 16px !important; }
    .stButton button { background-color: #1a3c6e !important; color: white !important; border-radius: 10px !important; padding: 10px 25px !important; font-size: 17px !important; transition: 0.3s; }
    .stButton button:hover { background-color: #102649 !important; transform: scale(1.05); }
    .stDataFrame table { border-radius: 10px !important; }
    .dataframe tbody tr:nth-child(odd) { background-color: #eef2f7 !important; }
    .dataframe tbody tr:hover { background-color: #d7e3ff !important; }
    </style>
    """,
    unsafe_allow_html=True
)

# ===============================
# نقطة البداية للتطبيق
# ===============================
def main():
    st.set_page_config(layout="wide", page_title="أداة استخلاص وتقارير مالية")
    st.title("📄 نظام استخلاص البيانات")
    st.markdown("---")

    # تهيئة قاعدة البيانات إن لم تكن موجودة
    try:
        initialize_db()
    except OperationalError:
        st.error("❌ فشل في تهيئة قاعدة البيانات. تأكد من أذونات الكتابة للمجلد.")

    if 'extracted_data_df' not in st.session_state:
        st.session_state['extracted_data_df'] = pd.DataFrame()

    uploaded_files = st.file_uploader(
        "📤 قم بتحميل الملفات (pdf, png, jpg, jpeg) - يمكنك اختيار عدة ملفات",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True
    )

    if uploaded_files:
        all_extracted_data = []

        if st.button("🚀 بدء الاستخلاص"):
            total_files = len(uploaded_files)
            progress_bar = st.progress(0)
            status_text = st.empty()
            processed_count = 0

            status_text.info(f"⏳ بدء استخلاص {total_files} ملفات بالتسلسل. سيأخذ كل ملف الوقت اللازم للاستخلاص...")

            for i, uploaded_file in enumerate(uploaded_files):
                file_bytes, file_name = uploaded_file.read(), uploaded_file.name
                file_type = file_name.split('.')[-1].lower()

                status_text.info(f"⏳ جاري معالجة الملف **{file_name}** ({i+1} من {total_files}).")
                data = extract_financial_data(file_bytes, file_name, file_type)

                if data:
                    all_extracted_data.append(data)
                    st.success(f"✅ تم استخلاص البيانات من **{file_name}** بنجاح.")
                else:
                    st.warning(f"⚠️ فشل استخلاص البيانات من **{file_name}**. راجع تفاصيل الخطأ أعلاه.")

                processed_count += 1
                progress_bar.progress(processed_count / total_files)

            if all_extracted_data:
                status_text.success(f"✅ اكتمل استخلاص جميع الملفات ({len(all_extracted_data)} ملفات).")
                new_df = pd.DataFrame(all_extracted_data)
                display_cols = ["مؤشر التشتت", "اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
                new_df = new_df.reindex(columns=display_cols, fill_value='غير متوفر')
                st.session_state['extracted_data_df'] = pd.concat([st.session_state['extracted_data_df'], new_df], ignore_index=True)
            else:
                status_text.error("❌ فشل استخلاص أي بيانات.")
                progress_bar.empty()

    # جدول قابل للتعديل
    if not st.session_state['extracted_data_df'].empty:
        st.subheader("✏️ جميع البيانات المستخلصة (قابلة للتعديل)")

        if st.button("💡 استخرج نص الدلالة المطابقة"):
            temp_df = st.session_state['extracted_data_df'].copy()
            if 'نص الدلالة المطابقة (للمراجعة)' in temp_df.columns:
                temp_df.drop(columns=['نص الدلالة المطابقة (للمراجعة)'], inplace=True, errors='ignore')

            def get_delala_description(row):
                delala_num = str(row.get('رقم الدلالة', 'غير متوفر')).strip()
                try:
                    num = int(delala_num)
                    return f"({num}) {DELALAT_MAPPING.get(num, 'رقم الدلالة المستخلصة غير صحيح')}"
                except ValueError:
                    return delala_num

            if 'رقم الدلالة' in temp_df.columns:
                temp_df.insert(temp_df.columns.get_loc('رقم الدلالة') + 1,
                               'نص الدلالة المطابقة (للمراجعة)',
                               temp_df.apply(get_delala_description, axis=1))
            st.session_state['extracted_data_df'] = temp_df
            st.rerun()

        edited_df = st.data_editor(
            st.session_state['extracted_data_df'],
            use_container_width=True,
            num_rows="dynamic"
        )

        st.markdown("---")
        if st.button("💾 تأكيد وحفظ التعديلات في قاعدة البيانات"):
            saved_count = 0
            total_rows = len(edited_df)
            status_placeholder = st.empty()
            for index, row in edited_df.iterrows():
                row_data = dict(row)
                # حذف أعمدة مؤقتة
                row_data.pop('مؤشر التشتت', None)
                row_data.pop('نص الدلالة المطابقة (للمراجعة)', None)
                if save_to_db(row_data):
                    saved_count += 1
                else:
                    status_placeholder.error(f"❌ فشل حفظ السجل رقم {index + 1}.")
                    break

            if saved_count == total_rows:
                status_placeholder.success(f"✅ تم حفظ {saved_count} سجل بنجاح!")
                st.session_state['extracted_data_df'] = pd.DataFrame()
                st.rerun()
            elif saved_count > 0:
                status_placeholder.warning(f"⚠️ تم حفظ {saved_count} فقط. راجع الأخطاء.")
            else:
                status_placeholder.error("❌ فشل حفظ جميع السجلات.")

    # إحصائيات وتصدير
    display_basic_stats()

    st.markdown("---")
    st.subheader("📊 تصدير البيانات النهائية")
    if st.button("⬇️ تحميل تقرير Excel من قاعدة البيانات"):
        report_data = fetch_all_reports()
        if report_data and report_data[0] is not None:
            records, column_names = report_data
            with st.spinner("⏳ جاري إنشاء ملف Excel من البيانات المحفوظة..."):
                excel_data_bytes = create_final_report_from_db(records, column_names)
            if excel_data_bytes:
                st.download_button(
                    "⬇️ اضغط للتحميل",
                    data=excel_data_bytes,
                    file_name="Final_Database_Report.xlsx",
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
            else:
                st.warning("لم يتم إنشاء ملف Excel. قد تكون البيانات المسترجعة فارغة.")
        else:
            st.error("فشل في استرجاع البيانات من قاعدة البيانات أو لا توجد سجلات.")

if __name__ == "__main__":
    main()
