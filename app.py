import streamlit as st
import pandas as pd
from google import genai
import os
from arabic_reshaper import reshape
from bidi.algorithm import get_display

# ----------------------------------------------------------------
# إعدادات الـ API الآمنة (يقرأ المفتاح من st.secrets عند النشر)
# ----------------------------------------------------------------
# عند التشغيل على Streamlit Cloud، هذا السطر سيقرأ المفتاح الذي قمتِ بحفظه في Secrets
try:
    # نقوم بتهيئة متغير البيئة ليعمل مع مكتبة google-genai
    os.environ['GEMINI_API_KEY'] = st.secrets["gemini_api_key"]
    # ونقوم بتهيئة العميل (Client) باستخدام المفتاح الآمن
    client = genai.Client(api_key=st.secrets["gemini_api_key"])
except AttributeError:
    # هذه الرسالة تظهر فقط إذا حاولتِ التشغيل محلياً دون إعداد Secrets
    st.error(get_display(reshape("لم يتم العثور على مفتاح API. يرجى إعداد st.secrets أو استخدام بيئة Streamlit Cloud.")))
    client = None # منع تشغيل التطبيق دون مفتاح

# ----------------------------------------------------------------
# وظائف معالجة النصوص والملفات
# ----------------------------------------------------------------

def reshape_text(text):
    """يعالج النصوص العربية لضمان العرض الصحيح (من اليمين لليسار)."""
    if text:
        return get_display(reshape(text))
    return text

def extract_insights(file_content, client):
    """يتواصل مع نموذج Gemini لاستخراج الأفكار الرئيسية."""
    if not client:
        return reshape_text("العميل غير مهيأ بسبب عدم وجود مفتاح API.")
        
    # رسالة النظام لضبط سلوك النموذج
    system_prompt = ("أنت محلل مالي خبير. مهمتك هي تحليل البيانات المالية المقدمة "
                     "واستخراج 5 نقاط رئيسية حول الأداء المالي، "
                     "و3 مخاطر محتملة، وكتابة ملخص تنفيذي مقنع بأسلوب عربي فصيح ومهني. "
                     "يجب أن يكون الناتج بصيغة نصية منظمة ومرتبة مع استخدام العناوين الفرعية لتسهيل القراءة.")

    # إعداد الطلب
    prompt = f"قم بتحليل البيانات المالية التالية واستخراج الأفكار كما هو مطلوب: \n\n{file_content}"
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={"system_instruction": system_prompt}
        )
        return response.text
    except Exception as e:
        return f"حدث خطأ في الاتصال بالنموذج: {e}"

# ----------------------------------------------------------------
# واجهة Streamlit (العرض على الويب)
# ----------------------------------------------------------------

def main():
    st.set_page_config(page_title=reshape_text("محلل تقارير مالية بالذكاء الاصطناعي"), layout="wide")
    st.title(reshape_text("💡 محلل التقارير المالية (بالذكاء الاصطناعي)"))

    # التأكد من تهيئة العميل قبل المتابعة
    if 'client' not in globals() or not client:
        return

    with st.sidebar:
        st.header(reshape_text("التعليمات"))
        st.write(reshape_text("قم بتحميل ملف بيانات مالية (CSV/Excel) وسأقوم بتحليله لك."))
        
        uploaded_file = st.file_uploader(reshape_text("اختر ملف CSV أو Excel"), type=["csv", "xlsx"])

    if uploaded_file is not None:
        file_details = {"FileName": uploaded_file.name, "FileType": uploaded_file.type}
        st.subheader(reshape_text(f"تحليل الملف: {file_details['FileName']}"))

        # قراءة البيانات
        try:
            if uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(uploaded_file)
            else:
                df = pd.read_excel(uploaded_file)
            
            st.dataframe(df.head()) # عرض أول 5 صفوف
            
            # تحويل البيانات إلى نص ليحلله النموذج
            file_content = df.to_markdown(index=False)
            
            st.markdown("---")
            
            # زر التحليل
            if st.button(reshape_text("بدء التحليل باستخدام Gemini"), key="analyze_button"):
                with st.spinner(reshape_text('جاري تحليل البيانات... قد يستغرق الأمر بعض الوقت...')):
                    analysis_result = extract_insights(file_content, client)
                    
                st.success(reshape_text("✅ تم الانتهاء من التحليل"))
                
                # عرض النتيجة
                st.markdown(analysis_result)
                st.markdown("---")
                st.download_button(
                    label=reshape_text("⬇️ تحميل ملخص التحليل"),
                    data=analysis_result.encode('utf-8'),
                    file_name="financial_analysis_summary.md",
                    mime='text/markdown'
                )

        except Exception as e:
            st.error(reshape_text(f"حدث خطأ في قراءة الملف: {e}"))
    else:
        st.info(reshape_text("يرجى تحميل ملف مالي للبدء."))

if __name__ == '__main__':
    main()
```
eof

### 🚀 الخطوة التالية (مهمة جداً)

الآن بعد أن أصبح لديكِ ملف **`app.py`** سليم على جهازكِ:

1.  **الملف الثاني:** تأكدي من أن لديكِ أيضاً ملف **`requirements.txt`** يحتوي على المكتبات التالية (يمكنكِ إنشاؤه أيضاً بنفس الطريقة ولصق المحتوى):
    ```
    streamlit==1.51.0
    google-genai==1.52.0
    pandas==2.2.2
    xlsxwriter==3.2.9
    arabic-reshaper==3.0.0
    python-bidi==0.6.7