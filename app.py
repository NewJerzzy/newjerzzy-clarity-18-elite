import streamlit as st

class Clarity18Elite:
    def __init__(self):
        self.bankroll = 1000

engine = Clarity18Elite()

st.title("CLARITY 18.0 ELITE")
st.write(f"Bankroll: ${engine.bankroll}")
st.success("Engine loaded successfully!")
