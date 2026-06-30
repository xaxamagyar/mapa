import streamlit as st

st.set_page_config(
    page_title="Firemní ERP Systém",
    page_icon="🏢",
    layout="centered"
)

st.title("🏢 Firemní systém a Logistika")
st.markdown("---")

st.markdown("Vítejte v hlavním rozcestníku. Vyberte si modul, se kterým chcete pracovat:")

col1, col2 = st.columns(2)

with col1:
    st.info("🚚 **PLÁNOVAČ TRAS A DISPEČINK**\n\nModul pro stahování objednávek, plánování rozvozů na mapě a tisk PDF pro řidiče i sklad.")
    if st.button("Spustit Plánovač tras", type="primary", use_container_width=True):
        st.switch_page("pages/1_Planovac_Tras.py")

with col2:
    st.success("💰 **KALKULACE MARŽE A FINANCE**\n\nModul pro zadávání nákupních cen, faktur a výpočet čistého zisku na objednávku.")
    if st.button("Spustit Kalkulaci zisku", type="primary", use_container_width=True):
        st.switch_page("pages/2_Kalkulace_Marze.py")