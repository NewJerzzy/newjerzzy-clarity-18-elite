import streamlit as st

st.set_page_config(page_title="CLARITY TEST", layout="wide")
st.title("🔮 CLARITY 18.0 ELITE - TEST MODE")
st.markdown("If you see this, Streamlit is working correctly.")

st.success("✅ The app loaded successfully!")

# Simple test button
if st.button("Click me"):
    st.write("Button works!")
